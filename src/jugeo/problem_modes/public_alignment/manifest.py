"""Manifest, capability flags, and provenance constants for public_alignment.

This module declares the full machine-readable manifest for the
``public_alignment`` package, which implements Chapter 13 of
``preliminaries/theory2.tex``.

Theory basis
------------
Chapter 13 of theory2.tex introduces the *public-honesty algebra*: a
collection of constraints on the functor

    π_pub : InternalState → PublicDocumentation

The central constraint is the **monotonicity law**: for any trust level T
attached to an internal judgment, the projected public claim may carry a
trust level of at most T.  Formally:

    ∀ J ∈ InternalState.  trust(π_pub(J)) ≤ trust(J)

Violations of this law are called *silent-strengthening obstructions* and
are treated as Ȟ¹ cohomology classes on the semantic site.  They are never
silently erased; they must be either repaired (by weakening the public claim)
or explicitly flagged as known violations in the manifest.

Capability flags enumerate the operations this package supports so that the
orchestration layer can check prerequisites before invoking any stage.

# copilot: manifest module for public_alignment — Ch13 provenance
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Type aliases (mirror jugeo-wide conventions)
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# ---------------------------------------------------------------------------
# §1  Module-level provenance constants
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, JsonValue] = {
    "stage": "ch13-public-alignment",
    "sequence": 13,
    "semantic_source": "preliminaries/theory2.tex",
    "chapter": 13,
    "chapter_title": "Documentation, Migration, and Public Honesty",
    "theory_version": "2.0",
    "package_root": "jugeo.problem_modes.public_alignment",
    "generated_by": "jugeo-codegen",
    "copilot_channel": "oracle_proposed",
    "description": (
        "Enforces the monotonicity constraint on the public-projection map: "
        "public documentation may weaken but never strengthen internal trust claims."
    ),
    "key_concepts": [
        "honest_projection",
        "publicity_boundary",
        "silent_strengthening_obstruction",
        "documentation_conservativity",
        "migration_semantic_preservation",
    ],
}

# ---------------------------------------------------------------------------
# §2  Theorem targets from Ch13
# ---------------------------------------------------------------------------

THEOREM_TARGETS: tuple[str, ...] = (
    "theorem_honesty_monotonicity",
    "theorem_projection_conservativity",
    "theorem_publicity_boundary_soundness",
    "theorem_migration_semantic_preservation",
    "theorem_documentation_faithfulness",
    "theorem_silent_strengthening_impossibility",
    "theorem_trust_ceiling_admissibility",
    "theorem_honest_projection_functor_naturality",
    "theorem_migration_plan_honesty",
    "theorem_public_claim_weaken_idempotence",
    "lemma_projection_composition_conservative",
    "lemma_trust_delta_non_negative",
    "lemma_boundary_crossing_monotone",
    "corollary_no_silent_upgrade",
    "corollary_migration_preserves_semantics",
)

# ---------------------------------------------------------------------------
# §3  Capability flags
# ---------------------------------------------------------------------------

class PublicAlignmentCap(str, Enum):
    """Capability flags for the public_alignment package.

    Each flag represents an operation or guarantee that the package provides.
    The orchestration layer checks these flags before invoking stages.

    Attributes
    ----------
    HONESTY_ENFORCEMENT
        The package can validate that public outputs do not strengthen
        internal claims (Ȟ¹-violation detection).
    DOCUMENTATION_PROJECTION
        The package can project internal judgment state to documentation
        sections under the conservative-projection functor.
    MIGRATION_ANALYSIS
        The package can analyze API/documentation changes and produce
        honest migration plans preserving semantic content.
    PUBLICITY_BOUNDARY_MANAGEMENT
        The package can manage the internal/public state boundary and
        enforce audience-specific trust ceilings.
    TRUST_CEILING_APPLICATION
        The package can apply a trust ceiling to any projection, ensuring
        that no public claim exceeds the declared ceiling for its audience.
    SILENT_STRENGTHENING_DETECTION
        The package can detect any projection that silently upgrades trust
        beyond the internal level (Ȟ¹ class generation).
    MIGRATION_CERTIFICATE_EMISSION
        The package can emit a machine-readable certificate confirming that
        a migration plan is honest and semantically preserving.
    BATCH_HONESTY_CHECK
        The package can efficiently batch-check honesty across many
        projections simultaneously.
    HONESTY_REPAIR
        The package can automatically repair dishonest claims by weakening
        them to match internal trust levels.
    PROOF_OBLIGATION_GENERATION
        The package can generate formal proof obligations for Ch13 theorems.
    """

    HONESTY_ENFORCEMENT = "honesty_enforcement"
    DOCUMENTATION_PROJECTION = "documentation_projection"
    MIGRATION_ANALYSIS = "migration_analysis"
    PUBLICITY_BOUNDARY_MANAGEMENT = "publicity_boundary_management"
    TRUST_CEILING_APPLICATION = "trust_ceiling_application"
    SILENT_STRENGTHENING_DETECTION = "silent_strengthening_detection"
    MIGRATION_CERTIFICATE_EMISSION = "migration_certificate_emission"
    BATCH_HONESTY_CHECK = "batch_honesty_check"
    HONESTY_REPAIR = "honesty_repair"
    PROOF_OBLIGATION_GENERATION = "proof_obligation_generation"


# ---------------------------------------------------------------------------
# §4  Capability declaration dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PublicAlignmentCapability:
    """Declaration of a single capability provided by the package.

    Attributes
    ----------
    cap : PublicAlignmentCap
        The capability flag.
    description : str
        Human-readable description of what the capability entails.
    theory_reference : str
        Citation in theory2.tex that motivates this capability.
    is_required : bool
        Whether this capability is required for the package to function.
    depends_on : tuple[PublicAlignmentCap, ...]
        Other capabilities this one depends on.
    status : str
        One of ``"stable"``, ``"experimental"``, ``"planned"``.
    """

    cap: PublicAlignmentCap
    description: str
    theory_reference: str
    is_required: bool = True
    depends_on: tuple[PublicAlignmentCap, ...] = ()
    status: str = "stable"

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            A dictionary with string keys and JSON-serializable values.
        """
        return {
            "cap": self.cap.value,
            "description": self.description,
            "theory_reference": self.theory_reference,
            "is_required": self.is_required,
            "depends_on": [c.value for c in self.depends_on],
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> PublicAlignmentCapability:
        """Deserialize from a JSON-compatible dictionary.

        Parameters
        ----------
        data : dict[str, JsonValue]
            Dictionary previously produced by ``to_dict()``.

        Returns
        -------
        PublicAlignmentCapability
            Reconstructed instance.
        """
        return cls(
            cap=PublicAlignmentCap(str(data["cap"])),
            description=str(data["description"]),
            theory_reference=str(data["theory_reference"]),
            is_required=bool(data.get("is_required", True)),
            depends_on=tuple(
                PublicAlignmentCap(v)
                for v in (data.get("depends_on") or [])  # type: ignore[union-attr]
            ),
            status=str(data.get("status", "stable")),
        )


# ---------------------------------------------------------------------------
# §5  Canonical capability declarations
# ---------------------------------------------------------------------------

_CAPABILITY_DECLARATIONS: tuple[PublicAlignmentCapability, ...] = (
    PublicAlignmentCapability(
        cap=PublicAlignmentCap.HONESTY_ENFORCEMENT,
        description=(
            "Validates that every public output claim carries a trust level "
            "at most equal to the corresponding internal judgment trust level. "
            "Detects and records Ȟ¹ silent-strengthening obstructions."
        ),
        theory_reference="theory2.tex §13.2 – Honesty Monotonicity Law",
        is_required=True,
        status="stable",
    ),
    PublicAlignmentCapability(
        cap=PublicAlignmentCap.DOCUMENTATION_PROJECTION,
        description=(
            "Projects internal judgment state to public documentation sections "
            "using the conservative HonestProjection functor π_pub.  Guarantees "
            "proj(T) ≤ T for all trust levels T."
        ),
        theory_reference="theory2.tex §13.3 – Documentation as Conservative Projection",
        is_required=True,
        status="stable",
    ),
    PublicAlignmentCapability(
        cap=PublicAlignmentCap.MIGRATION_ANALYSIS,
        description=(
            "Analyzes changes between old and new judgment states, produces a "
            "MigrationPlan that is honest about breaking changes and preserves "
            "semantic content across versions."
        ),
        theory_reference="theory2.tex §13.5 – Migration Semantic Preservation",
        is_required=True,
        depends_on=(PublicAlignmentCap.HONESTY_ENFORCEMENT,),
        status="stable",
    ),
    PublicAlignmentCapability(
        cap=PublicAlignmentCap.PUBLICITY_BOUNDARY_MANAGEMENT,
        description=(
            "Manages the trust boundary between internal state and public "
            "declarations.  Enforces per-audience trust ceilings and audits "
            "all registered projections for compliance."
        ),
        theory_reference="theory2.tex §13.4 – The Publicity Boundary",
        is_required=True,
        depends_on=(
            PublicAlignmentCap.HONESTY_ENFORCEMENT,
            PublicAlignmentCap.TRUST_CEILING_APPLICATION,
        ),
        status="stable",
    ),
    PublicAlignmentCapability(
        cap=PublicAlignmentCap.TRUST_CEILING_APPLICATION,
        description=(
            "Applies a TrustLevel ceiling to a projection so that no claim in "
            "the resulting public documentation exceeds the ceiling, regardless "
            "of what the internal judgment asserts."
        ),
        theory_reference="theory2.tex §13.4.1 – Audience Trust Ceilings",
        is_required=False,
        status="stable",
    ),
    PublicAlignmentCapability(
        cap=PublicAlignmentCap.SILENT_STRENGTHENING_DETECTION,
        description=(
            "Scans a set of PublicClaims against their corresponding internal "
            "Judgments and flags any claim whose declared_trust_level exceeds "
            "the internal trust level (Ȟ¹ violation)."
        ),
        theory_reference="theory2.tex §13.2.3 – Silent Strengthening as Ȟ¹ Obstruction",
        is_required=True,
        depends_on=(PublicAlignmentCap.HONESTY_ENFORCEMENT,),
        status="stable",
    ),
    PublicAlignmentCapability(
        cap=PublicAlignmentCap.MIGRATION_CERTIFICATE_EMISSION,
        description=(
            "Emits a machine-readable StructuredFailure (used as a certificate "
            "here, with severity=INFO) confirming that a migration plan is honest "
            "and semantically preserving."
        ),
        theory_reference="theory2.tex §13.5.4 – Migration Certificates",
        is_required=False,
        depends_on=(
            PublicAlignmentCap.MIGRATION_ANALYSIS,
            PublicAlignmentCap.HONESTY_ENFORCEMENT,
        ),
        status="stable",
    ),
    PublicAlignmentCapability(
        cap=PublicAlignmentCap.BATCH_HONESTY_CHECK,
        description=(
            "Efficiently checks honesty across many projections in a single "
            "pass, collecting all ObstructionRecords without short-circuiting."
        ),
        theory_reference="theory2.tex §13.2.5 – Batch Validation",
        is_required=False,
        depends_on=(PublicAlignmentCap.HONESTY_ENFORCEMENT,),
        status="stable",
    ),
    PublicAlignmentCapability(
        cap=PublicAlignmentCap.HONESTY_REPAIR,
        description=(
            "Automatically repairs dishonest claims by weakening them to match "
            "the internal trust level.  Produces a new PublicClaim whose "
            "declared_trust_level equals the internal level."
        ),
        theory_reference="theory2.tex §13.2.6 – Canonical Repair by Weakening",
        is_required=False,
        depends_on=(PublicAlignmentCap.HONESTY_ENFORCEMENT,),
        status="stable",
    ),
    PublicAlignmentCapability(
        cap=PublicAlignmentCap.PROOF_OBLIGATION_GENERATION,
        description=(
            "Generates formal proof obligations for Ch13 theorems, tying each "
            "obligation to the concrete projection or migration plan that must "
            "satisfy it."
        ),
        theory_reference="theory2.tex §13.7 – Proof Obligations",
        is_required=False,
        depends_on=(
            PublicAlignmentCap.HONESTY_ENFORCEMENT,
            PublicAlignmentCap.DOCUMENTATION_PROJECTION,
        ),
        status="experimental",
    ),
)


# ---------------------------------------------------------------------------
# §6  Manifest dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PublicAlignmentManifest:
    """Full machine-readable manifest for the public_alignment package.

    The manifest serves as the authoritative declaration of what this package
    provides, what theory it implements, and what capabilities it exposes.
    It is consulted by the orchestration layer before invoking any stage and
    by the test suite to verify completeness.

    Attributes
    ----------
    package_name : str
        Fully-qualified Python package name.
    stage : str
        Theory stage identifier (``"ch13-public-alignment"``).
    sequence : int
        Ordinal position in the full theory sequence.
    semantic_source : str
        Path to the theory document this package implements.
    theorem_targets : tuple[str, ...]
        Names of all theorems this package is responsible for.
    capabilities : tuple[PublicAlignmentCapability, ...]
        Declared capabilities.
    provenance : dict[str, JsonValue]
        Full provenance dictionary (mirrors ``MANIFEST_SPEC_PROVENANCE``).
    created_at : str
        ISO-8601 timestamp of when this manifest was created.
    version : str
        Semantic version of the manifest itself.
    is_validated : bool | None
        ``True`` after ``validate_manifest()`` has been called and passed,
        ``False`` if validation found issues, ``None`` if not yet validated.
    """

    package_name: str
    stage: str
    sequence: int
    semantic_source: str
    theorem_targets: tuple[str, ...]
    capabilities: tuple[PublicAlignmentCapability, ...]
    provenance: dict[str, JsonValue]
    created_at: str
    version: str = "1.0.0"
    is_validated: bool | None = None

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def has_capability(self, cap: PublicAlignmentCap) -> bool:
        """Return ``True`` when *cap* is declared in this manifest.

        Parameters
        ----------
        cap : PublicAlignmentCap
            The capability to check for.

        Returns
        -------
        bool
            ``True`` if the capability is declared.
        """
        return any(c.cap == cap for c in self.capabilities)

    def required_capabilities(self) -> tuple[PublicAlignmentCapability, ...]:
        """Return the subset of capabilities marked ``is_required=True``.

        Returns
        -------
        tuple[PublicAlignmentCapability, ...]
            Required capabilities only.
        """
        return tuple(c for c in self.capabilities if c.is_required)

    def capability_by_flag(
        self, cap: PublicAlignmentCap
    ) -> PublicAlignmentCapability | None:
        """Return the capability declaration for *cap*, or ``None``.

        Parameters
        ----------
        cap : PublicAlignmentCap
            The capability flag to look up.

        Returns
        -------
        PublicAlignmentCapability | None
            The matching declaration, or ``None`` if not present.
        """
        for c in self.capabilities:
            if c.cap == cap:
                return c
        return None

    def theorem_count(self) -> int:
        """Return the number of theorem targets declared.

        Returns
        -------
        int
            Number of theorems in ``theorem_targets``.
        """
        return len(self.theorem_targets)

    def summary(self) -> str:
        """Return a one-line human-readable summary of this manifest.

        Returns
        -------
        str
            Summary string.
        """
        cap_count = len(self.capabilities)
        req_count = len(self.required_capabilities())
        thm_count = self.theorem_count()
        return (
            f"PublicAlignmentManifest(stage={self.stage!r}, "
            f"sequence={self.sequence}, "
            f"capabilities={cap_count} ({req_count} required), "
            f"theorems={thm_count}, "
            f"validated={self.is_validated})"
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize the manifest to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            Fully serialized manifest.
        """
        return {
            "package_name": self.package_name,
            "stage": self.stage,
            "sequence": self.sequence,
            "semantic_source": self.semantic_source,
            "theorem_targets": list(self.theorem_targets),
            "capabilities": [c.to_dict() for c in self.capabilities],
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
            "version": self.version,
            "is_validated": self.is_validated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> PublicAlignmentManifest:
        """Deserialize from a JSON-compatible dictionary.

        Parameters
        ----------
        data : dict[str, JsonValue]
            Dictionary previously produced by ``to_dict()``.

        Returns
        -------
        PublicAlignmentManifest
            Reconstructed manifest.
        """
        caps = tuple(
            PublicAlignmentCapability.from_dict(c)  # type: ignore[arg-type]
            for c in (data.get("capabilities") or [])
        )
        return cls(
            package_name=str(data["package_name"]),
            stage=str(data["stage"]),
            sequence=int(data["sequence"]),  # type: ignore[arg-type]
            semantic_source=str(data["semantic_source"]),
            theorem_targets=tuple(str(t) for t in (data.get("theorem_targets") or [])),
            capabilities=caps,
            provenance=dict(data.get("provenance") or {}),  # type: ignore[arg-type]
            created_at=str(data["created_at"]),
            version=str(data.get("version", "1.0.0")),
            is_validated=data.get("is_validated"),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# §7  Singleton manifest instance
# ---------------------------------------------------------------------------

PUBLIC_ALIGNMENT_MANIFEST: PublicAlignmentManifest = PublicAlignmentManifest(
    package_name="jugeo.problem_modes.public_alignment",
    stage="ch13-public-alignment",
    sequence=13,
    semantic_source="preliminaries/theory2.tex",
    theorem_targets=THEOREM_TARGETS,
    capabilities=_CAPABILITY_DECLARATIONS,
    provenance=MANIFEST_SPEC_PROVENANCE,
    created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    version="1.0.0",
    is_validated=None,
)


# ---------------------------------------------------------------------------
# §8  Public API functions
# ---------------------------------------------------------------------------

def get_manifest() -> PublicAlignmentManifest:
    """Return the singleton ``PUBLIC_ALIGNMENT_MANIFEST``.

    Returns
    -------
    PublicAlignmentManifest
        The canonical manifest for this package.
    """
    return PUBLIC_ALIGNMENT_MANIFEST


def validate_manifest(manifest: PublicAlignmentManifest | None = None) -> tuple[bool, list[str]]:
    """Validate the given manifest (or the singleton) for internal consistency.

    Checks performed:

    * All required capabilities are present.
    * No duplicate capability flags.
    * All ``depends_on`` references name capabilities that are also declared.
    * At least one theorem target is declared.
    * ``sequence`` is a positive integer.
    * ``semantic_source`` is non-empty.

    Parameters
    ----------
    manifest : PublicAlignmentManifest | None
        Manifest to validate.  If ``None``, validates the singleton.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if valid; ``(False, [error, ...])`` otherwise.
    """
    if manifest is None:
        manifest = PUBLIC_ALIGNMENT_MANIFEST

    errors: list[str] = []

    # Check for duplicate caps
    seen_caps: set[str] = set()
    for cap_decl in manifest.capabilities:
        key = cap_decl.cap.value
        if key in seen_caps:
            errors.append(f"Duplicate capability declared: {key!r}")
        seen_caps.add(key)

    # Check depends_on references
    for cap_decl in manifest.capabilities:
        for dep in cap_decl.depends_on:
            if not manifest.has_capability(dep):
                errors.append(
                    f"Capability {cap_decl.cap.value!r} depends on "
                    f"{dep.value!r} which is not declared."
                )

    # Check theorem targets
    if not manifest.theorem_targets:
        errors.append("No theorem targets declared.")

    # Check sequence
    if manifest.sequence <= 0:
        errors.append(f"Invalid sequence number: {manifest.sequence}")

    # Check semantic_source
    if not manifest.semantic_source.strip():
        errors.append("Empty semantic_source.")

    # Check required capabilities present
    required_flags = {
        PublicAlignmentCap.HONESTY_ENFORCEMENT,
        PublicAlignmentCap.DOCUMENTATION_PROJECTION,
        PublicAlignmentCap.MIGRATION_ANALYSIS,
        PublicAlignmentCap.PUBLICITY_BOUNDARY_MANAGEMENT,
        PublicAlignmentCap.SILENT_STRENGTHENING_DETECTION,
    }
    for flag in required_flags:
        if not manifest.has_capability(flag):
            errors.append(f"Required capability missing: {flag.value!r}")

    is_valid = len(errors) == 0
    return is_valid, errors


def manifest_to_dict(manifest: PublicAlignmentManifest | None = None) -> dict[str, JsonValue]:
    """Serialize a manifest (or the singleton) to a JSON-compatible dict.

    Parameters
    ----------
    manifest : PublicAlignmentManifest | None
        Manifest to serialize.  If ``None``, serializes the singleton.

    Returns
    -------
    dict[str, JsonValue]
        JSON-compatible dictionary.
    """
    if manifest is None:
        manifest = PUBLIC_ALIGNMENT_MANIFEST
    return manifest.to_dict()


def describe_capability(cap: PublicAlignmentCap) -> str:
    """Return the description of a capability from the singleton manifest.

    Parameters
    ----------
    cap : PublicAlignmentCap
        The capability to describe.

    Returns
    -------
    str
        Description string, or ``"(not declared)"`` if missing.
    """
    decl = PUBLIC_ALIGNMENT_MANIFEST.capability_by_flag(cap)
    if decl is None:
        return "(not declared)"
    return decl.description


def list_theorem_targets() -> tuple[str, ...]:
    """Return the tuple of all theorem targets from the singleton manifest.

    Returns
    -------
    tuple[str, ...]
        Theorem target names.
    """
    return PUBLIC_ALIGNMENT_MANIFEST.theorem_targets


# ---------------------------------------------------------------------------
# §9  Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # Provenance constants
    "MANIFEST_SPEC_PROVENANCE",
    "THEOREM_TARGETS",
    # Enumerations
    "PublicAlignmentCap",
    # Dataclasses
    "PublicAlignmentCapability",
    "PublicAlignmentManifest",
    # Singleton
    "PUBLIC_ALIGNMENT_MANIFEST",
    # Functions
    "get_manifest",
    "validate_manifest",
    "manifest_to_dict",
    "describe_capability",
    "list_theorem_targets",
]

# copilot: manifest.py — Ch13 public_alignment provenance and capability declarations
