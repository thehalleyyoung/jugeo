"""Package manifest for ``jugeo.foundations.type_objects``.

This module exposes the authoritative subsystem manifest for the JuGeo
type-object layer.  References theory2.tex Ch3.

    "A JuGeo type is not merely an annotation — it is a coordinate-indexed
    semantic object with a carrier, transport maps, gluing laws, support, and
    trust."

The manifest records which capabilities are enabled, the canonical dependency
order of the type-object files, and the theorems the subsystem must ultimately
discharge.  It is consumed by the orchestration layer to validate that the
type-object layer is in a coherent state before downstream subsystems are
initialised.

Provenance
----------
MODULE_AUTHOR : str
    "copilot"
THEORY_REF : str
    "theory2.tex Ch3"
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Mapping

__all__ = [
    # Constants
    "TYPE_OBJECTS_STAGE",
    "TYPE_OBJECTS_SEQUENCE",
    "TYPE_OBJECTS_PROVENANCE",
    "THEOREM_TARGETS",
    "MODULE_AUTHOR",
    # Enumerations
    "TypeObjectCapabilityFlag",
    # Dataclasses
    "TypeObjectCapability",
    "TypeObjectsManifest",
    # Singleton
    "MANIFEST",
]

# ---------------------------------------------------------------------------
# Module-level provenance constants
# ---------------------------------------------------------------------------

MODULE_AUTHOR: Final[str] = "copilot"
THEORY_REF: Final[str] = "theory2.tex Ch3"

TYPE_OBJECTS_STAGE: Final[str] = "foundations"
"""The pipeline stage that owns this subsystem."""

TYPE_OBJECTS_SEQUENCE: Final[int] = 3
"""Ordinal position within the ``foundations`` stage (1-based)."""

TYPE_OBJECTS_PROVENANCE: Final[Mapping[str, str | int]] = MappingProxyType(
    {
        "semantic_source": THEORY_REF,
        "stage": TYPE_OBJECTS_STAGE,
        "sequence": TYPE_OBJECTS_SEQUENCE,
        "target_file": "jugeo/foundations/type_objects/",
        "author": MODULE_AUTHOR,
    }
)
"""Immutable provenance record for the type-objects subsystem.

The mapping follows the convention used across all JuGeo subsystem manifests:
``semantic_source`` names the theory chapter, ``stage`` names the pipeline
stage, ``sequence`` gives ordinal position, ``target_file`` is the canonical
import path, and ``author`` records who generated the file.
"""

THEOREM_TARGETS: Final[tuple[str, ...]] = (
    "carrier identity",
    "transport coherence",
    "gluing uniqueness",
    "support monotonicity",
    "restriction functoriality",
    "trust monotonicity",
    "type comparison reflexivity",
    "coordinate indexing faithfulness",
    "carrier extension unitality",
    "transport composition associativity",
)
"""Ordered tuple of theorem names that the type-objects subsystem must discharge.

Each name corresponds to a formal statement in theory2.tex Ch3.  The orchestration
layer tracks which theorems have been verified (∧ all proofs closed) vs merely
proposed (φ ⊢ τ at trust ≥ ORACLE_PROPOSED).

Theorem summary
---------------
carrier identity
    The identity morphism on c induces the identity transport on K(c).
transport coherence
    ρ(f ∘ g) = ρ(g) ∘ ρ(f) for composable morphisms f, g (contravariance).
gluing uniqueness
    Given a compatible family {sᵢ ∈ K(Uᵢ)} the glued section s ∈ K(U) is
    unique (sheaf condition).
support monotonicity
    If c ⪯ c' and τ is supported at c then τ is supported at c'.
restriction functoriality
    ρ respects composition and identities — it is a presheaf morphism.
trust monotonicity
    Promotion of trust along a verification chain is monotone: trust(τ) ≤
    trust(promote(τ)).
type comparison reflexivity
    Every type τ satisfies τ ≤ τ under the canonical type comparison ⪯.
coordinate indexing faithfulness
    The coordinate-indexing functor Φ: TypeObj → Site is faithful.
carrier extension unitality
    Extending K along the identity morphism returns K unchanged.
transport composition associativity
    (ρ(f) ∘ ρ(g)) ∘ ρ(h) = ρ(f) ∘ (ρ(g) ∘ ρ(h)) for all composable triples.
"""


# ---------------------------------------------------------------------------
# Capability flag enumeration
# ---------------------------------------------------------------------------


class TypeObjectCapabilityFlag(str, Enum):
    """Enumeration of semantic capabilities a type-object subsystem may expose.

    Each flag names a facet of the full τ = (c, K, ρ, γ, supp, trust) structure
    defined in theory2.tex Ch3.  The manifest records whether each facet is
    currently *enabled* in this build of JuGeo.

    Attributes
    ----------
    CARRIER
        The type carrier K — the underlying set / data structure of inhabitants.
    TRANSPORT
        The transport/restriction family ρ: Hom(c', c) → Hom(K(c), K(c')).
    GLUING
        The gluing law γ assembling a global type from a compatible covering family.
    SUPPORT
        The support supp(τ) ⊆ Obj(C) — coordinates where τ is non-trivial.
    RESTRICTION
        Explicit restriction morphisms (a special case of transport).
    TRUST
        The trust annotation trust ∈ TrustLevel on the type assignment.
    INFERENCE
        Automated type inference (constructing τ from partial data).
    INTEGRATION
        Integration with the descent-locality layer (sheaf gluing across patches).
    """

    CARRIER = "carrier"
    TRANSPORT = "transport"
    GLUING = "gluing"
    SUPPORT = "support"
    RESTRICTION = "restriction"
    TRUST = "trust"
    INFERENCE = "inference"
    INTEGRATION = "integration"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _validate_capability_name(name: str) -> None:
    """Raise ``ValueError`` if *name* is not a known ``TypeObjectCapabilityFlag`` value.

    Parameters
    ----------
    name : str
        The capability name to validate.

    Raises
    ------
    ValueError
        If *name* does not match any ``TypeObjectCapabilityFlag`` member value.
    """
    valid = {flag.value for flag in TypeObjectCapabilityFlag}
    if name not in valid:
        raise ValueError(
            f"Unknown capability name {name!r}. "
            f"Expected one of: {sorted(valid)}"
        )


def _coerce_to_tuple_of_str(value: Any) -> tuple[str, ...]:
    """Coerce *value* to a ``tuple[str, ...]``, returning empty tuple for ``None``.

    Parameters
    ----------
    value : Any
        An iterable of strings, a single string, or ``None``.

    Returns
    -------
    tuple[str, ...]
        A normalised tuple of strings.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(x) for x in value)


def _capability_summary_line(cap: "TypeObjectCapability") -> str:
    """Return a single-line human-readable summary of *cap*.

    Parameters
    ----------
    cap : TypeObjectCapability
        The capability to summarise.

    Returns
    -------
    str
        A formatted string like ``"[✓] carrier  (foundations)  — <rationale>"``
        or ``"[✗] inference (foundations) — <rationale>"``.
    """
    tick = "✓" if cap.enabled else "✗"
    return f"[{tick}] {cap.name:<14} ({cap.stage})  — {cap.rationale}"


# ---------------------------------------------------------------------------
# TypeObjectCapability dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TypeObjectCapability:
    """An immutable record describing one capability of the type-object layer.

    Each ``TypeObjectCapability`` corresponds to a facet of the full JuGeo type
    structure τ = (c, K, ρ, γ, supp, trust) as defined in theory2.tex Ch3.

    Parameters
    ----------
    name : str
        Must match one of the ``TypeObjectCapabilityFlag`` values.
    enabled : bool
        Whether the capability is active in the current build.
    stage : str
        Pipeline stage that owns this capability (usually ``"foundations"``).
    rationale : str
        Human-readable explanation of why the capability is enabled/disabled.
    surfaced_by : tuple[str, ...]
        Module paths that surface this capability to callers.
    authority_boundary : str
        Describes the authority scope within which the capability is valid.
    honest_scope : str
        Plain English statement of what the capability *does not* claim to do.

    Raises
    ------
    ValueError
        If *name* is not a recognised ``TypeObjectCapabilityFlag`` value.
    """

    name: str
    enabled: bool
    stage: str
    rationale: str
    surfaced_by: tuple[str, ...] = field(default_factory=tuple)
    authority_boundary: str = ""
    honest_scope: str = ""

    def __post_init__(self) -> None:
        """Validate that *name* is a known capability flag.

        Raises
        ------
        ValueError
            If ``self.name`` is not a member value of ``TypeObjectCapabilityFlag``.
        """
        _validate_capability_name(self.name)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Return ``True`` if this capability is active.

        Returns
        -------
        bool
            The value of ``self.enabled``.
        """
        return self.enabled

    def flag(self) -> TypeObjectCapabilityFlag:
        """Return the corresponding ``TypeObjectCapabilityFlag`` member.

        Returns
        -------
        TypeObjectCapabilityFlag
            The enum member whose ``.value`` equals ``self.name``.
        """
        return TypeObjectCapabilityFlag(self.name)

    def disable(self, reason: str) -> "TypeObjectCapability":
        """Return a new ``TypeObjectCapability`` with ``enabled=False``.

        Parameters
        ----------
        reason : str
            Updated rationale explaining why the capability was disabled.

        Returns
        -------
        TypeObjectCapability
            A copy of *self* with ``enabled=False`` and ``rationale=reason``.
        """
        return replace(self, enabled=False, rationale=reason)

    def enable(self, reason: str) -> "TypeObjectCapability":
        """Return a new ``TypeObjectCapability`` with ``enabled=True``.

        Parameters
        ----------
        reason : str
            Updated rationale explaining why the capability was enabled.

        Returns
        -------
        TypeObjectCapability
            A copy of *self* with ``enabled=True`` and ``rationale=reason``.
        """
        return replace(self, enabled=True, rationale=reason)

    def add_surface(self, module_path: str) -> "TypeObjectCapability":
        """Return a new capability with *module_path* added to ``surfaced_by``.

        Parameters
        ----------
        module_path : str
            A dotted Python module path to add.

        Returns
        -------
        TypeObjectCapability
            A copy of *self* with the module path appended to ``surfaced_by``.
        """
        if module_path in self.surfaced_by:
            return self
        return replace(self, surfaced_by=self.surfaced_by + (module_path,))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Keys: ``name``, ``enabled``, ``stage``, ``rationale``,
            ``surfaced_by``, ``authority_boundary``, ``honest_scope``.
        """
        return {
            "name": self.name,
            "enabled": self.enabled,
            "stage": self.stage,
            "rationale": self.rationale,
            "surfaced_by": list(self.surfaced_by),
            "authority_boundary": self.authority_boundary,
            "honest_scope": self.honest_scope,
        }

    def serialize(self) -> dict[str, Any]:
        """Alias for ``to_dict()`` matching the subsystem serialisation convention.

        Returns
        -------
        dict[str, Any]
            Identical to the result of ``to_dict()``.
        """
        return self.to_dict()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TypeObjectCapability":
        """Reconstruct a ``TypeObjectCapability`` from a serialised dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by ``to_dict()`` / ``serialize()``.

        Returns
        -------
        TypeObjectCapability
            The reconstructed instance.

        Raises
        ------
        KeyError
            If required keys (``name``, ``enabled``, ``stage``, ``rationale``)
            are absent from *data*.
        ValueError
            If ``data["name"]`` is not a recognised capability flag.
        """
        return cls(
            name=data["name"],
            enabled=bool(data["enabled"]),
            stage=data["stage"],
            rationale=data["rationale"],
            surfaced_by=tuple(data.get("surfaced_by", ())),
            authority_boundary=data.get("authority_boundary", ""),
            honest_scope=data.get("honest_scope", ""),
        )

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "TypeObjectCapability":
        """Alias for ``from_dict()`` matching the subsystem parse convention.

        Parameters
        ----------
        data : dict[str, Any]
            A serialised ``TypeObjectCapability`` dictionary.

        Returns
        -------
        TypeObjectCapability
            The reconstructed instance.
        """
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# TypeObjectsManifest dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TypeObjectsManifest:
    """The authoritative manifest for the ``jugeo.foundations.type_objects`` subsystem.

    Records capabilities, theorem targets, dependency order, provenance, and
    version information.  Consumed by the orchestration layer during pipeline
    initialisation to verify subsystem coherence before downstream work begins.

    Theory reference: theory2.tex Ch3.  The manifest is the *meta*-description
    of the type-object layer; the types themselves are defined in ``models.py``.

    Parameters
    ----------
    version : str
        Semantic version string for this manifest (e.g. ``"0.3.0"``).
    stage : str
        Pipeline stage owning this manifest (``"foundations"``).
    sequence : int
        Ordinal position within the stage (1-based).
    description : str
        Human-readable description of the subsystem.
    capabilities : tuple[TypeObjectCapability, ...]
        All capabilities tracked by this manifest.
    theorem_targets : tuple[str, ...]
        Names of theorems that the subsystem must discharge.
    dependency_order : tuple[str, ...]
        Canonical import order of files within the subsystem.
    author : str
        Who generated/owns this manifest (``"copilot"``).
    provenance : Mapping[str, Any]
        Full provenance mapping (semantic source, stage, sequence, etc.).
    """

    version: str
    stage: str
    sequence: int
    description: str
    capabilities: tuple[TypeObjectCapability, ...]
    theorem_targets: tuple[str, ...]
    dependency_order: tuple[str, ...]
    author: str
    provenance: Mapping[str, Any]

    # ------------------------------------------------------------------
    # Capability queries
    # ------------------------------------------------------------------

    def capability(self, name: str) -> TypeObjectCapability | None:
        """Return the ``TypeObjectCapability`` whose name equals *name*, or ``None``.

        Parameters
        ----------
        name : str
            A ``TypeObjectCapabilityFlag`` value string, e.g. ``"carrier"``.

        Returns
        -------
        TypeObjectCapability | None
            The matching capability record, or ``None`` if not found.
        """
        for cap in self.capabilities:
            if cap.name == name:
                return cap
        return None

    def enabled_capabilities(self) -> tuple[TypeObjectCapability, ...]:
        """Return only the capabilities that are currently enabled.

        Returns
        -------
        tuple[TypeObjectCapability, ...]
            A sub-tuple of ``self.capabilities`` containing only enabled entries.
        """
        return tuple(c for c in self.capabilities if c.enabled)

    def disabled_capabilities(self) -> tuple[TypeObjectCapability, ...]:
        """Return only the capabilities that are currently disabled.

        Returns
        -------
        tuple[TypeObjectCapability, ...]
            A sub-tuple of ``self.capabilities`` containing only disabled entries.
        """
        return tuple(c for c in self.capabilities if not c.enabled)

    def has_capability(self, flag: TypeObjectCapabilityFlag) -> bool:
        """Return ``True`` if the named capability is present *and* enabled.

        Parameters
        ----------
        flag : TypeObjectCapabilityFlag
            The capability to check.

        Returns
        -------
        bool
            ``True`` iff a capability record with the flag's value exists and
            ``enabled`` is ``True``.
        """
        cap = self.capability(flag.value)
        return cap is not None and cap.enabled

    # ------------------------------------------------------------------
    # Counting / structural queries
    # ------------------------------------------------------------------

    def dependency_count(self) -> int:
        """Return the number of files in the canonical dependency order.

        Returns
        -------
        int
            ``len(self.dependency_order)``.
        """
        return len(self.dependency_order)

    def theorem_count(self) -> int:
        """Return the number of theorem targets registered in this manifest.

        Returns
        -------
        int
            ``len(self.theorem_targets)``.
        """
        return len(self.theorem_targets)

    def capability_count(self) -> int:
        """Return the total number of capability records (enabled + disabled).

        Returns
        -------
        int
            ``len(self.capabilities)``.
        """
        return len(self.capabilities)

    def enabled_count(self) -> int:
        """Return the number of enabled capabilities.

        Returns
        -------
        int
            Count of capabilities where ``enabled`` is ``True``.
        """
        return sum(1 for c in self.capabilities if c.enabled)

    # ------------------------------------------------------------------
    # Consistency check
    # ------------------------------------------------------------------

    def is_consistent(self) -> bool:
        """Return ``True`` if the manifest passes internal consistency checks.

        Consistency requires:

        1. All capability names are distinct.
        2. All capability names are valid ``TypeObjectCapabilityFlag`` values.
        3. The dependency order contains no duplicate entries.
        4. The theorem targets contain no duplicate entries.
        5. ``version`` is a non-empty string.
        6. ``author`` is a non-empty string.

        Returns
        -------
        bool
            ``True`` if all checks pass, ``False`` otherwise.
        """
        # (1) Distinct capability names
        cap_names = [c.name for c in self.capabilities]
        if len(cap_names) != len(set(cap_names)):
            return False

        # (2) Valid capability names
        valid_flags = {f.value for f in TypeObjectCapabilityFlag}
        for name in cap_names:
            if name not in valid_flags:
                return False

        # (3) Distinct dependency order entries
        if len(self.dependency_order) != len(set(self.dependency_order)):
            return False

        # (4) Distinct theorem targets
        if len(self.theorem_targets) != len(set(self.theorem_targets)):
            return False

        # (5) Non-empty version
        if not self.version.strip():
            return False

        # (6) Non-empty author
        if not self.author.strip():
            return False

        return True

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this manifest.

        The summary includes the version, stage, author, capability status,
        theorem target count, and dependency order.  It is intended for
        display in CLI output and log files.

        Returns
        -------
        str
            A formatted multi-line string.
        """
        lines: list[str] = [
            f"TypeObjectsManifest v{self.version}",
            f"  stage      : {self.stage} (sequence={self.sequence})",
            f"  author     : {self.author}",
            f"  description: {self.description}",
            f"  consistent : {self.is_consistent()}",
            "",
            f"  Capabilities ({self.enabled_count()}/{self.capability_count()} enabled):",
        ]
        for cap in self.capabilities:
            lines.append(f"    {_capability_summary_line(cap)}")

        lines.append("")
        lines.append(f"  Theorem targets ({self.theorem_count()}):")
        for i, target in enumerate(self.theorem_targets, 1):
            lines.append(f"    {i:2d}. {target}")

        lines.append("")
        lines.append(f"  Dependency order ({self.dependency_count()} files):")
        for i, dep in enumerate(self.dependency_order, 1):
            lines.append(f"    {i:2d}. {dep}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Mutation helpers (return new instances)
    # ------------------------------------------------------------------

    def with_capability_enabled(
        self, flag: TypeObjectCapabilityFlag, reason: str
    ) -> "TypeObjectsManifest":
        """Return a new manifest with the named capability enabled.

        Parameters
        ----------
        flag : TypeObjectCapabilityFlag
            The capability to enable.
        reason : str
            Rationale for enabling the capability.

        Returns
        -------
        TypeObjectsManifest
            A copy of *self* with the targeted capability set to ``enabled=True``.

        Raises
        ------
        KeyError
            If no capability matching *flag* is registered in this manifest.
        """
        updated = []
        found = False
        for cap in self.capabilities:
            if cap.name == flag.value:
                updated.append(cap.enable(reason))
                found = True
            else:
                updated.append(cap)
        if not found:
            raise KeyError(f"No capability {flag.value!r} found in manifest.")
        return replace(self, capabilities=tuple(updated))

    def with_capability_disabled(
        self, flag: TypeObjectCapabilityFlag, reason: str
    ) -> "TypeObjectsManifest":
        """Return a new manifest with the named capability disabled.

        Parameters
        ----------
        flag : TypeObjectCapabilityFlag
            The capability to disable.
        reason : str
            Rationale for disabling the capability.

        Returns
        -------
        TypeObjectsManifest
            A copy of *self* with the targeted capability set to ``enabled=False``.

        Raises
        ------
        KeyError
            If no capability matching *flag* is registered in this manifest.
        """
        updated = []
        found = False
        for cap in self.capabilities:
            if cap.name == flag.value:
                updated.append(cap.disable(reason))
                found = True
            else:
                updated.append(cap)
        if not found:
            raise KeyError(f"No capability {flag.value!r} found in manifest.")
        return replace(self, capabilities=tuple(updated))

    def with_theorem(self, theorem_name: str) -> "TypeObjectsManifest":
        """Return a new manifest with *theorem_name* appended to the theorem targets.

        If *theorem_name* is already present the manifest is returned unchanged.

        Parameters
        ----------
        theorem_name : str
            The name of the theorem to add.

        Returns
        -------
        TypeObjectsManifest
            Updated manifest.
        """
        if theorem_name in self.theorem_targets:
            return self
        return replace(self, theorem_targets=self.theorem_targets + (theorem_name,))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible nested dictionary.

        Returns
        -------
        dict[str, Any]
            All manifest fields serialised to primitive types.
        """
        return {
            "version": self.version,
            "stage": self.stage,
            "sequence": self.sequence,
            "description": self.description,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "theorem_targets": list(self.theorem_targets),
            "dependency_order": list(self.dependency_order),
            "author": self.author,
            "provenance": dict(self.provenance),
        }

    def serialize(self) -> dict[str, Any]:
        """Alias for ``to_dict()`` matching the subsystem serialisation convention.

        Returns
        -------
        dict[str, Any]
            Identical to the result of ``to_dict()``.
        """
        return self.to_dict()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TypeObjectsManifest":
        """Reconstruct a ``TypeObjectsManifest`` from a serialised dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by ``to_dict()`` / ``serialize()``.

        Returns
        -------
        TypeObjectsManifest
            The reconstructed manifest.

        Raises
        ------
        KeyError
            If required top-level keys are absent.
        """
        capabilities = tuple(
            TypeObjectCapability.from_dict(c) for c in data.get("capabilities", [])
        )
        return cls(
            version=data["version"],
            stage=data["stage"],
            sequence=int(data["sequence"]),
            description=data.get("description", ""),
            capabilities=capabilities,
            theorem_targets=tuple(data.get("theorem_targets", ())),
            dependency_order=tuple(data.get("dependency_order", ())),
            author=data.get("author", MODULE_AUTHOR),
            provenance=MappingProxyType(data.get("provenance", {})),
        )

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "TypeObjectsManifest":
        """Alias for ``from_dict()`` matching the subsystem parse convention.

        Parameters
        ----------
        data : dict[str, Any]
            A serialised ``TypeObjectsManifest`` dictionary.

        Returns
        -------
        TypeObjectsManifest
            The reconstructed instance.
        """
        return cls.from_dict(data)

    @classmethod
    def default(cls) -> "TypeObjectsManifest":
        """Return the canonical default manifest for the type-objects subsystem.

        This is equivalent to calling ``_build_default_manifest()`` and is the
        recommended way to obtain a fresh manifest without explicitly constructing
        all capability records.

        Returns
        -------
        TypeObjectsManifest
            The default manifest singleton value (a new equal instance each call).
        """
        return _build_default_manifest()


# ---------------------------------------------------------------------------
# Private manifest builder
# ---------------------------------------------------------------------------


def _build_default_manifest() -> TypeObjectsManifest:
    """Construct and return the canonical default ``TypeObjectsManifest``.

    This function is the single source of truth for the default manifest
    configuration.  It is called once at module load to create the
    ``MANIFEST`` singleton, and can be called again to produce fresh copies.

    Returns
    -------
    TypeObjectsManifest
        A fully populated, consistent manifest for the type-objects subsystem.

    Notes
    -----
    All eight core capabilities (carrier → integration) are included.
    INFERENCE and INTEGRATION start as disabled because those facets depend on
    the descent-locality layer which may not yet be initialised.
    """
    capabilities = (
        TypeObjectCapability(
            name=TypeObjectCapabilityFlag.CARRIER.value,
            enabled=True,
            stage=TYPE_OBJECTS_STAGE,
            rationale=(
                "The carrier K is the primary constituent of τ; it must always "
                "be available for any well-formed type object."
            ),
            surfaced_by=(
                "jugeo.foundations.type_objects.models",
                "jugeo.foundations.type_objects.manifest",
            ),
            authority_boundary="Any coordinate in the semantic site.",
            honest_scope=(
                "Does not validate inhabitant semantics beyond syntactic membership."
            ),
        ),
        TypeObjectCapability(
            name=TypeObjectCapabilityFlag.TRANSPORT.value,
            enabled=True,
            stage=TYPE_OBJECTS_STAGE,
            rationale=(
                "Transport ρ is required to move sections along morphisms; "
                "without it the presheaf structure collapses."
            ),
            surfaced_by=("jugeo.foundations.type_objects.models",),
            authority_boundary="Morphisms between coordinates in the same site.",
            honest_scope=(
                "Transport rules are syntactic strings; semantic validity is "
                "not machine-checked in this stage."
            ),
        ),
        TypeObjectCapability(
            name=TypeObjectCapabilityFlag.GLUING.value,
            enabled=True,
            stage=TYPE_OBJECTS_STAGE,
            rationale=(
                "Gluing law γ is the sheaf condition; it lets us assemble a global "
                "type from compatible local patches (theory2.tex §3.4)."
            ),
            surfaced_by=("jugeo.foundations.type_objects.models",),
            authority_boundary="Coverings defined by the site topology.",
            honest_scope=(
                "Gluing uniqueness is asserted (``is_verified`` flag) but is "
                "not formally proved in this layer."
            ),
        ),
        TypeObjectCapability(
            name=TypeObjectCapabilityFlag.SUPPORT.value,
            enabled=True,
            stage=TYPE_OBJECTS_STAGE,
            rationale=(
                "Support supp(τ) ⊆ Obj(C) tracks where τ is non-trivial and "
                "is needed for the support-monotonicity theorem."
            ),
            surfaced_by=("jugeo.foundations.type_objects.models",),
            authority_boundary="Object set of the semantic site.",
            honest_scope=(
                "Support is a frozenset of coordinate key strings; it is not "
                "a sub-category in the full categorical sense at this stage."
            ),
        ),
        TypeObjectCapability(
            name=TypeObjectCapabilityFlag.RESTRICTION.value,
            enabled=True,
            stage=TYPE_OBJECTS_STAGE,
            rationale=(
                "Restriction is a special-cased transport along inclusion morphisms; "
                "it appears frequently enough to warrant its own flag."
            ),
            surfaced_by=("jugeo.foundations.type_objects.models",),
            authority_boundary="Inclusion morphisms within the site.",
            honest_scope=(
                "Does not enforce that restriction and transport agree on the "
                "overlap — that is the responsibility of ``GluingLaw``."
            ),
        ),
        TypeObjectCapability(
            name=TypeObjectCapabilityFlag.TRUST.value,
            enabled=True,
            stage=TYPE_OBJECTS_STAGE,
            rationale=(
                "Trust annotation trust ∈ TrustLevel is required for the "
                "trust-monotonicity theorem and downstream judgment integration."
            ),
            surfaced_by=(
                "jugeo.foundations.type_objects.models",
                "jugeo.judgments.judgment_terms",
            ),
            authority_boundary=(
                "Trust values range over TrustLevel (CONTRADICTED … VERIFIED_PROOF)."
            ),
            honest_scope=(
                "Trust promotion does not re-run any proofs; it records a claim "
                "that must be independently discharged."
            ),
        ),
        TypeObjectCapability(
            name=TypeObjectCapabilityFlag.INFERENCE.value,
            enabled=False,
            stage=TYPE_OBJECTS_STAGE,
            rationale=(
                "Automated type inference is not yet implemented; it is staged "
                "for a later pipeline sequence after the oracle-federation layer."
            ),
            surfaced_by=(),
            authority_boundary="N/A — not yet active.",
            honest_scope=(
                "When enabled, inference will produce types at trust ≤ ORACLE_PROPOSED "
                "unless independently verified."
            ),
        ),
        TypeObjectCapability(
            name=TypeObjectCapabilityFlag.INTEGRATION.value,
            enabled=False,
            stage=TYPE_OBJECTS_STAGE,
            rationale=(
                "Descent-locality integration requires the "
                "``jugeo.foundations.descent_locality`` subsystem to be fully "
                "initialised first (dependency order constraint)."
            ),
            surfaced_by=(),
            authority_boundary="N/A — not yet active.",
            honest_scope=(
                "When enabled, types will participate in the global sheaf gluing "
                "protocol implemented in ``descent_locality.integration``."
            ),
        ),
    )

    return TypeObjectsManifest(
        version="0.3.0",
        stage=TYPE_OBJECTS_STAGE,
        sequence=TYPE_OBJECTS_SEQUENCE,
        description=(
            "Type-object layer for JuGeo: coordinate-indexed semantic types "
            "τ = (c, K, ρ, γ, supp, trust) as defined in theory2.tex Ch3."
        ),
        capabilities=capabilities,
        theorem_targets=THEOREM_TARGETS,
        dependency_order=(
            "jugeo.foundations.type_objects.manifest",
            "jugeo.foundations.type_objects.models",
        ),
        author=MODULE_AUTHOR,
        provenance=MappingProxyType(dict(TYPE_OBJECTS_PROVENANCE)),
    )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

MANIFEST: TypeObjectsManifest = _build_default_manifest()
"""The canonical ``TypeObjectsManifest`` singleton for this subsystem.

Import and use this object directly:

.. code-block:: python

    from jugeo.foundations.type_objects.manifest import MANIFEST

    if MANIFEST.has_capability(TypeObjectCapabilityFlag.GLUING):
        ...  # gluing-dependent logic

The singleton is constructed once at import time.  Use
``TypeObjectsManifest.default()`` or ``_build_default_manifest()`` to obtain
a fresh equal instance if needed.
"""
