r"""Package manifest for the pack_federation encoding module.

Theory (theory2.tex §35 — Pack Federation as Sheaves):
    The pack federation encoding is a categorical construction that
    treats a collection of local semantic packs as an open cover of
    a global meaning space.  Each pack contributes a local section;
    bridge theorems specify how sections restrict and glue across
    overlapping vocabularies.

    The manifest object defined here captures the version, capabilities,
    and exported public API surface of the pack_federation sub-package,
    providing a machine-readable record that downstream tooling can query
    to determine whether a given installation supports a required
    capability (e.g. SHEAF_ENCODING vs DESCENT_PROTOCOL).

    §35 Lemma 35.1: A valid pack-federation manifest is one in which
    every declared capability has at least one exported class that
    implements the corresponding interface.

Public surface
--------------
:data:`PACK_FEDERATION_VERSION`
    Semantic version string for this sub-package.
:data:`PACK_FEDERATION_CHAPTER_REF`
    Canonical theory reference string.
:class:`PackFederationCapability`
    Enumeration of feature capabilities.
:class:`PackFederationManifest`
    Dataclass carrying full manifest state.
:func:`build_default_manifest`
    Factory for the canonical manifest.
:func:`validate_manifest`
    Validate a manifest instance, returning errors.

copilot: pack-federation-manifest
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Final, FrozenSet, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

__all__: list[str] = [
    "PACK_FEDERATION_VERSION",
    "PACK_FEDERATION_CHAPTER_REF",
    "PackFederationCapability",
    "PackFederationManifest",
    "build_default_manifest",
    "validate_manifest",
]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

PACK_FEDERATION_VERSION: Final[str] = "0.1.0"
"""Semantic version for the pack_federation encoding sub-package."""

PACK_FEDERATION_CHAPTER_REF: Final[str] = "theory2.tex §35"
"""Canonical reference into the theory document."""

PACK_FEDERATION_DESCRIPTION: Final[str] = (
    "Encoding of pack federation as sheaves of local semantic theories, "
    "bridge theorems as morphisms between pack vocabularies, and federation "
    "protocol as descent across pack boundaries (theory2.tex §35)."
)

# ---------------------------------------------------------------------------
# Capability enumeration
# ---------------------------------------------------------------------------


class PackFederationCapability(str, Enum):
    """Feature capabilities exposed by the pack_federation sub-package.

    Each member corresponds to a distinct theoretical concept introduced in
    theory2.tex §35.  Consumers can check whether a manifest declares a
    given capability before attempting to use the corresponding classes.

    Attributes
    ----------
    SHEAF_ENCODING:
        Support for modelling pack federations as sheaves of local sections.
    BRIDGE_MORPHISMS:
        Support for bridge theorems treated as categorical morphisms.
    DESCENT_PROTOCOL:
        Support for federating evidence via descent across pack boundaries.
    COHOMOLOGY_COMPUTATION:
        Support for computing sheaf cohomology groups H^0 and H^1.
    OVERLAP_VALIDATION:
        Support for validating overlap laws at shared pack boundaries.
    """

    SHEAF_ENCODING = "sheaf_encoding"
    BRIDGE_MORPHISMS = "bridge_morphisms"
    DESCENT_PROTOCOL = "descent_protocol"
    COHOMOLOGY_COMPUTATION = "cohomology_computation"
    OVERLAP_VALIDATION = "overlap_validation"


# ---------------------------------------------------------------------------
# Manifest dataclass
# ---------------------------------------------------------------------------


@dataclass
class PackFederationManifest:
    """Manifest record for the pack_federation encoding sub-package.

    Carries version information, declared capabilities, and lists of
    exported symbols.  Intended to be constructed once at package
    initialisation time via :func:`build_default_manifest` and stored
    as a module-level singleton, but it is fully mutable so that tests
    and downstream tools can augment it.

    Parameters
    ----------
    manifest_id:
        UUID string uniquely identifying this manifest instance.
    version:
        Semantic version string; defaults to :data:`PACK_FEDERATION_VERSION`.
    chapter_ref:
        Theory document reference; defaults to :data:`PACK_FEDERATION_CHAPTER_REF`.
    capabilities:
        List of :class:`PackFederationCapability` members declared.
    exported_classes:
        Names of exported classes.
    exported_functions:
        Names of exported standalone functions.
    created_at:
        Unix timestamp of manifest creation.
    description:
        Human-readable description string.

    copilot: manifest-dataclass
    """

    manifest_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: str = field(default=PACK_FEDERATION_VERSION)
    chapter_ref: str = field(default=PACK_FEDERATION_CHAPTER_REF)
    capabilities: list[PackFederationCapability] = field(default_factory=list)
    exported_classes: list[str] = field(default_factory=list)
    exported_functions: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    description: str = field(default=PACK_FEDERATION_DESCRIPTION)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> bool:
        """Return True iff the manifest passes all internal consistency checks.

        Checks performed:
        - manifest_id is a non-empty string parseable as a UUID.
        - version follows ``MAJOR.MINOR.PATCH`` format.
        - chapter_ref is non-empty.
        - capabilities is a non-empty list of :class:`PackFederationCapability`.
        - exported_classes is non-empty.
        - created_at is a positive float.

        Returns
        -------
        bool
            ``True`` if all checks pass, ``False`` otherwise.
        """
        if not self.manifest_id:
            return False
        try:
            uuid.UUID(self.manifest_id)
        except ValueError:
            return False

        parts = self.version.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            return False

        if not self.chapter_ref:
            return False

        if not isinstance(self.capabilities, list) or len(self.capabilities) == 0:
            return False

        if not isinstance(self.exported_classes, list) or len(self.exported_classes) == 0:
            return False

        if not isinstance(self.created_at, (int, float)) or self.created_at <= 0:
            return False

        return True

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the manifest to a plain dictionary.

        Returns
        -------
        dict
            Dictionary with all fields serialised to JSON-compatible types.
            Capability enum values are stored as their string values.
        """
        return {
            "manifest_id": self.manifest_id,
            "version": self.version,
            "chapter_ref": self.chapter_ref,
            "capabilities": [cap.value for cap in self.capabilities],
            "exported_classes": list(self.exported_classes),
            "exported_functions": list(self.exported_functions),
            "created_at": self.created_at,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PackFederationManifest:
        """Deserialise a manifest from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        PackFederationManifest
            Reconstructed manifest instance.

        Raises
        ------
        KeyError
            If a required key is absent from *d*.
        ValueError
            If a capability value in *d* is not a valid :class:`PackFederationCapability`.
        """
        caps: list[PackFederationCapability] = [
            PackFederationCapability(v) for v in d.get("capabilities", [])
        ]
        return cls(
            manifest_id=d["manifest_id"],
            version=d["version"],
            chapter_ref=d["chapter_ref"],
            capabilities=caps,
            exported_classes=list(d.get("exported_classes", [])),
            exported_functions=list(d.get("exported_functions", [])),
            created_at=float(d["created_at"]),
            description=d.get("description", ""),
        )

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_capability(self, cap: PackFederationCapability) -> None:
        """Declare a capability on this manifest if not already present.

        Parameters
        ----------
        cap:
            The :class:`PackFederationCapability` to add.
        """
        if cap not in self.capabilities:
            self.capabilities.append(cap)

    def register_export(self, symbol: str, kind: str) -> None:
        """Register an exported symbol.

        Parameters
        ----------
        symbol:
            The name of the exported symbol (class or function).
        kind:
            Either ``"class"`` or ``"function"``.

        Raises
        ------
        ValueError
            If *kind* is not ``"class"`` or ``"function"``.
        """
        if kind == "class":
            if symbol not in self.exported_classes:
                self.exported_classes.append(symbol)
        elif kind == "function":
            if symbol not in self.exported_functions:
                self.exported_functions.append(symbol)
        else:
            raise ValueError(f"Unknown export kind: {kind!r}. Expected 'class' or 'function'.")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this manifest.

        Returns
        -------
        str
            Formatted summary string suitable for printing.
        """
        cap_names = ", ".join(c.value for c in self.capabilities) or "(none)"
        cls_list = ", ".join(self.exported_classes) or "(none)"
        fn_list = ", ".join(self.exported_functions) or "(none)"
        return (
            f"PackFederationManifest\n"
            f"  id          : {self.manifest_id}\n"
            f"  version     : {self.version}\n"
            f"  chapter_ref : {self.chapter_ref}\n"
            f"  capabilities: {cap_names}\n"
            f"  classes     : {cls_list}\n"
            f"  functions   : {fn_list}\n"
            f"  created_at  : {self.created_at:.3f}\n"
            f"  description : {self.description[:80]}"
        )

    def get_capability_count(self) -> int:
        """Return the number of declared capabilities.

        Returns
        -------
        int
            Number of entries in :attr:`capabilities`.
        """
        return len(self.capabilities)

    def is_complete(self) -> bool:
        """Return True iff every known capability is declared and classes are exported.

        A *complete* manifest declares all five :class:`PackFederationCapability`
        members and has at least one exported class.

        Returns
        -------
        bool
            ``True`` if all capabilities present and exported_classes non-empty.
        """
        all_caps = set(PackFederationCapability)
        declared = set(self.capabilities)
        return all_caps.issubset(declared) and len(self.exported_classes) > 0

    def has_capability(self, cap: PackFederationCapability) -> bool:
        """Check whether a specific capability is declared.

        Parameters
        ----------
        cap:
            Capability to check.

        Returns
        -------
        bool
        """
        return cap in self.capabilities

    def get_export_count(self) -> int:
        """Return total number of exported symbols (classes + functions).

        Returns
        -------
        int
        """
        return len(self.exported_classes) + len(self.exported_functions)

    def merge_with(self, other: PackFederationManifest) -> PackFederationManifest:
        """Produce a new manifest that is the union of self and other.

        The resulting manifest inherits self's manifest_id and created_at.
        Capabilities and exports from *other* are added if not already present.

        Parameters
        ----------
        other:
            Another :class:`PackFederationManifest` to merge into this one.

        Returns
        -------
        PackFederationManifest
            A newly constructed merged manifest.
        """
        merged_caps = list(self.capabilities)
        for cap in other.capabilities:
            if cap not in merged_caps:
                merged_caps.append(cap)

        merged_classes = list(self.exported_classes)
        for cls_name in other.exported_classes:
            if cls_name not in merged_classes:
                merged_classes.append(cls_name)

        merged_fns = list(self.exported_functions)
        for fn_name in other.exported_functions:
            if fn_name not in merged_fns:
                merged_fns.append(fn_name)

        return PackFederationManifest(
            manifest_id=self.manifest_id,
            version=self.version,
            chapter_ref=self.chapter_ref,
            capabilities=merged_caps,
            exported_classes=merged_classes,
            exported_functions=merged_fns,
            created_at=self.created_at,
            description=self.description or other.description,
        )

    def diff(self, other: PackFederationManifest) -> dict[str, Any]:
        """Compute a diff between this manifest and another.

        Parameters
        ----------
        other:
            Manifest to compare against.

        Returns
        -------
        dict
            Dictionary with keys ``added_capabilities``, ``removed_capabilities``,
            ``added_classes``, ``removed_classes``, ``added_functions``,
            ``removed_functions``, and ``version_changed``.
        """
        self_caps = set(self.capabilities)
        other_caps = set(other.capabilities)
        self_cls = set(self.exported_classes)
        other_cls = set(other.exported_classes)
        self_fns = set(self.exported_functions)
        other_fns = set(other.exported_functions)

        return {
            "added_capabilities": [c.value for c in (other_caps - self_caps)],
            "removed_capabilities": [c.value for c in (self_caps - other_caps)],
            "added_classes": list(other_cls - self_cls),
            "removed_classes": list(self_cls - other_cls),
            "added_functions": list(other_fns - self_fns),
            "removed_functions": list(self_fns - other_fns),
            "version_changed": self.version != other.version,
        }


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

# Public classes in each sub-module of this package
_MODELS_CLASSES: Final[list[str]] = [
    "PackFederationEncoding",
    "BridgeTheoremEncoding",
    "FederationProtocol",
    "PackBoundary",
]

_S01_CLASSES: Final[list[str]] = ["PackFederationAsSheaf"]
_S02_CLASSES: Final[list[str]] = ["BridgeTheoremAsMorphism"]
_S03_CLASSES: Final[list[str]] = ["FederationProtocolEngine"]
_ALGORITHMS_FUNCTIONS: Final[list[str]] = [
    "compute_sheaf_condition",
    "find_minimal_bridge_path",
    "compute_federation_trust_ceiling",
    "validate_overlap_laws",
    "assemble_federation_result",
    "compute_pack_overlap_graph",
    "score_federation_quality",
]
_INTEGRATION_CLASSES: Final[list[str]] = ["PackFederationEncodingIntegration"]
_THEOREMS_FUNCTIONS: Final[list[str]] = [
    "verify_sheaf_condition_soundness",
    "verify_bridge_morphism_laws",
    "verify_federation_kind_preservation",
    "verify_trust_ceiling_monotonicity",
    "verify_overlap_law_consistency",
    "verify_descent_completeness",
    "verify_pack_boundary_coherence",
    "verify_federation_protocol_correctness",
]


def build_default_manifest() -> PackFederationManifest:
    """Construct the canonical :class:`PackFederationManifest` for this package.

    Registers all public classes from models, s01, s02, s03, and integration,
    all public functions from algorithms and theorems, and all five
    :class:`PackFederationCapability` values.

    Returns
    -------
    PackFederationManifest
        Fully-populated manifest ready for use.

    Notes
    -----
    This function is called at package initialisation time; the result is
    stored as :data:`DEFAULT_MANIFEST`.
    """
    m = PackFederationManifest()

    # --- capabilities ---
    for cap in PackFederationCapability:
        m.add_capability(cap)

    # --- classes ---
    all_classes = (
        _MODELS_CLASSES
        + _S01_CLASSES
        + _S02_CLASSES
        + _S03_CLASSES
        + _INTEGRATION_CLASSES
    )
    for cls_name in all_classes:
        m.register_export(cls_name, "class")

    # --- functions ---
    all_functions = _ALGORITHMS_FUNCTIONS + _THEOREMS_FUNCTIONS
    for fn_name in all_functions:
        m.register_export(fn_name, "function")

    return m


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------


def validate_manifest(m: PackFederationManifest) -> tuple[bool, list[str]]:
    """Validate a :class:`PackFederationManifest`, returning errors.

    Parameters
    ----------
    m:
        Manifest instance to validate.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if valid; ``(False, errors)`` where *errors* is a
        non-empty list of human-readable error strings if invalid.

    Notes
    -----
    This function performs more detailed checks than :meth:`PackFederationManifest.validate`,
    including checking that known capability values match the enum, that the
    version matches the expected package version, and that no duplicate entries
    are present in the export lists.
    """
    errors: list[str] = []

    # --- UUID ---
    if not m.manifest_id:
        errors.append("manifest_id is empty")
    else:
        try:
            uuid.UUID(m.manifest_id)
        except ValueError:
            errors.append(f"manifest_id {m.manifest_id!r} is not a valid UUID")

    # --- version ---
    parts = m.version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        errors.append(f"version {m.version!r} is not in MAJOR.MINOR.PATCH format")

    # --- chapter_ref ---
    if not m.chapter_ref:
        errors.append("chapter_ref is empty")

    # --- capabilities ---
    if not m.capabilities:
        errors.append("capabilities list is empty")
    else:
        valid_values = {cap.value for cap in PackFederationCapability}
        for cap in m.capabilities:
            if not isinstance(cap, PackFederationCapability):
                errors.append(f"capability {cap!r} is not a PackFederationCapability instance")
            elif cap.value not in valid_values:
                errors.append(f"capability value {cap.value!r} is not a known capability")
        # check for duplicates
        seen: set[PackFederationCapability] = set()
        for cap in m.capabilities:
            if cap in seen:
                errors.append(f"duplicate capability: {cap.value!r}")
            seen.add(cap)

    # --- exported_classes ---
    if not m.exported_classes:
        errors.append("exported_classes is empty")
    else:
        seen_cls: set[str] = set()
        for cls_name in m.exported_classes:
            if not isinstance(cls_name, str) or not cls_name:
                errors.append(f"invalid class name: {cls_name!r}")
            elif cls_name in seen_cls:
                errors.append(f"duplicate exported class: {cls_name!r}")
            seen_cls.add(cls_name)

    # --- exported_functions ---
    seen_fn: set[str] = set()
    for fn_name in m.exported_functions:
        if not isinstance(fn_name, str) or not fn_name:
            errors.append(f"invalid function name: {fn_name!r}")
        elif fn_name in seen_fn:
            errors.append(f"duplicate exported function: {fn_name!r}")
        seen_fn.add(fn_name)

    # --- created_at ---
    if not isinstance(m.created_at, (int, float)) or m.created_at <= 0:
        errors.append(f"created_at must be a positive float, got {m.created_at!r}")

    valid = len(errors) == 0
    return valid, errors


# ---------------------------------------------------------------------------
# Module-level default manifest singleton
# ---------------------------------------------------------------------------

DEFAULT_MANIFEST: PackFederationManifest = build_default_manifest()
"""The canonical manifest for this installation of pack_federation."""
