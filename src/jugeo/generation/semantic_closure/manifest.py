"""Semantic Closure Package Manifest and Public API Surface

# copilot: This module provides the manifest infrastructure for the
jugeo.generation.semantic_closure package. It defines the public API surface,
capability registry, exported symbol catalogue, and the machinery to build,
query, and summarize semantic closure manifests.

A SemanticClosureManifest is a frozen record of which capabilities and symbols are exported
at which TrustTier. Consumers can query the manifest to determine whether a given capability
is available, which modules are exported, and what the package version is.

The manifest is designed to be immutable and hashable so it can be embedded in judgment
tuples and used as a cache key. Judgments are tuples (c, phi, A, E, O, B, T, Pi).
Trust uses TrustTier enum. Obstructions are Cech H1 cohomology classes.
"""

from __future__ import annotations

import uuid
import hashlib
import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, List, Dict, FrozenSet, Tuple
import itertools
import functools
import datetime

try:
    from jugeo.core.trust import TrustTier
    from jugeo.core.judgment import Judgment
    from jugeo.core.obstruction import CechObstruction
except ImportError:
    from enum import Enum
    class TrustTier(Enum):
        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3
        RUNTIME_WITNESSED = 4
        PROOF_BACKED = 5
    Judgment = tuple

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

PACKAGE_NAME = "jugeo.generation.semantic_closure"

PACKAGE_VERSION = (0, 4, 1)

SCHEMA_VERSION = "sc-manifest-v4"

KNOWN_CAPABILITIES: Dict[str, str] = {
    "closure_check": "Verify that all semantic obligations have been discharged in a given cover.",
    "cover_proposal": "Propose a cover for a given topological space and schema.",
    "obligation_generation": "Generate semantic obligations from a cover proposal.",
    "local_verification": "Verify that local sections satisfy all locally-assigned obligations.",
    "global_gluing": "Assemble local verified sections into a global section using the gluing axiom.",
    "tier_promotion": "Promote a judgment from one TrustTier to the next upon passing all checks.",
    "residual_gap_analysis": "Identify and describe residual gaps left after partial closure.",
    "evidence_accumulation": "Accumulate evidence items that collectively discharge a set of obligations.",
    "semantic_consistency": "Check that the semantic assignments across open sets are mutually consistent.",
    "manifest_registry": "Build, query, and summarize semantic closure manifests at runtime.",
}

TIER_LABELS: Dict[Any, str] = {
    TrustTier.PROPOSAL: "Proposal (unverified, lowest trust)",
    TrustTier.REVIEWED: "Reviewed (peer-examined, intermediate trust)",
    TrustTier.VERIFIED: "Verified (machine-checked, high trust)",
    TrustTier.RUNTIME_WITNESSED: "Runtime-Witnessed (empirically confirmed, very high trust)",
    TrustTier.PROOF_BACKED: "Proof-Backed (formally proved, highest trust)",
}

DEFAULT_EXPORTED_MODULES: List[str] = [
    "jugeo.generation.semantic_closure.manifest",
    "jugeo.generation.semantic_closure.models",
    "jugeo.generation.semantic_closure.algorithms",
    "jugeo.generation.semantic_closure.integration",
    "jugeo.generation.semantic_closure.closure_checking",
    "jugeo.generation.semantic_closure.semantic_closure_completion_criter",
    "jugeo.generation.semantic_closure.integration_closure",
]

DEPRECATED_SYMBOLS: List[str] = [
    "legacy_closure_check",
    "OldManifestRecord",
    "closure_check_v1",
    "build_manifest_v0",
    "DeprecatedCapabilityRegistry",
]

# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticClosureManifest:
    """Immutable record of the semantic closure package's public API surface.

    A SemanticClosureManifest captures the set of capabilities and exported modules
    for the jugeo.generation.semantic_closure package at a specific TrustTier.
    Because the dataclass is frozen, instances are hashable and can be embedded
    in judgment tuples or used as dictionary keys without fear of mutation.

    The manifest is constructed once at package load time via build_manifest() and
    subsequently queried. Consumers should not construct manifests directly but
    should use the factory function to ensure all fields are consistently populated.
    """

    package_name: str
    version: Tuple
    capabilities: tuple
    exported_modules: FrozenSet
    manifest_tier: TrustTier

    def version_str(self) -> str:
        """Format the version tuple as a human-readable 'major.minor.patch' string.

        This method accesses each component of the version tuple by index position,
        handles tuples that are shorter than three elements by substituting zero for
        any missing component, and returns the formatted string. If the version field
        is completely absent or evaluates to a falsy value, the fallback string '0.0.0'
        is returned. The method is designed to be safe against malformed version tuples
        so that manifests built from incomplete metadata do not raise exceptions.
        Consumers can rely on this method always returning a non-empty dotted string.

        Returns:
            str: The version formatted as 'major.minor.patch'.
        """
        if not self.version:
            return "0.0.0"
        major = self.version[0] if len(self.version) > 0 else 0
        minor = self.version[1] if len(self.version) > 1 else 0
        patch = self.version[2] if len(self.version) > 2 else 0
        return "{}.{}.{}".format(major, minor, patch)

    def has_capability(self, cap: str) -> bool:
        """Determine whether a named capability is exported by this manifest.

        The capability identifier is first checked for None and empty-string cases,
        which both return False immediately. The method then strips leading and trailing
        whitespace from the supplied identifier before performing the membership test
        against self.capabilities. This normalisation ensures that callers passing
        capability strings with incidental surrounding spaces still receive the correct
        answer. The check is case-sensitive to avoid inadvertent capability aliasing
        across different naming conventions.

        Args:
            cap: The capability identifier to test.

        Returns:
            bool: True if the capability is present in the manifest, False otherwise.
        """
        if cap is None:
            return False
        stripped = cap.strip()
        if not stripped:
            return False
        return stripped in self.capabilities

    def exports_module(self, mod: str) -> bool:
        """Check whether the given module path is part of this manifest's exports.

        The module path argument is normalised by stripping leading and trailing
        whitespace before the membership test is performed against self.exported_modules.
        A None argument is handled gracefully by returning False without raising.
        This method is useful for runtime guards that must verify a module is part
        of the official public surface before importing symbols from it.
        The check is exact-match so that sub-package paths do not accidentally match
        parent package paths.

        Args:
            mod: The dotted module path to test.

        Returns:
            bool: True if the module is in the exported set, False otherwise.
        """
        if mod is None:
            return False
        normalised = mod.strip()
        if not normalised:
            return False
        return normalised in self.exported_modules

    def to_judgment_tuple(self) -> tuple:
        """Serialise this manifest to a plain tuple suitable for embedding in a Judgment.

        The tuple format is fixed and versioned via SCHEMA_VERSION so that consumers
        can detect format changes. The tuple contains the package name, the formatted
        version string, the count of capabilities, the count of exported modules,
        the tier name, and the schema version string. This representation is hashable
        and can be compared for equality without requiring the full manifest object.
        The method never raises and always returns a tuple of exactly six elements.

        Returns:
            tuple: A six-element summary tuple for this manifest.
        """
        return (
            self.package_name,
            self.version_str(),
            len(self.capabilities),
            len(self.exported_modules),
            self.manifest_tier.name,
            SCHEMA_VERSION,
        )

    def manifest_summary(self) -> str:
        """Produce a multi-line human-readable summary of this manifest.

        The summary includes all significant fields: package name, version string,
        trust tier with its human-readable label, the list of capabilities, the set
        of exported modules, and a timestamp indicating when the summary was generated.
        The timestamp is in ISO 8601 format using UTC so that the summary is
        reproducible in logs and reports. Deprecated symbols are also listed to help
        consumers identify APIs they should migrate away from. The method returns a
        single string with embedded newlines and is safe to pass directly to print()
        or a logging handler.

        Returns:
            str: Multi-line summary string.
        """
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        tier_label = TIER_LABELS.get(self.manifest_tier, self.manifest_tier.name)
        cap_lines = "\n".join("  - {}".format(c) for c in sorted(self.capabilities))
        mod_lines = "\n".join("  - {}".format(m) for m in sorted(self.exported_modules))
        dep_lines = "\n".join("  - {}".format(s) for s in DEPRECATED_SYMBOLS)
        return (
            "=== SemanticClosureManifest ===\n"
            "Package   : {}\n"
            "Version   : {}\n"
            "Tier      : {}\n"
            "Schema    : {}\n"
            "Generated : {}\n"
            "Capabilities ({}):\n{}\n"
            "Exported Modules ({}):\n{}\n"
            "Deprecated Symbols:\n{}\n"
        ).format(
            self.package_name,
            self.version_str(),
            tier_label,
            SCHEMA_VERSION,
            ts,
            len(self.capabilities),
            cap_lines,
            len(self.exported_modules),
            mod_lines,
            dep_lines,
        )


@dataclass(frozen=True)
class ClosureCapability:
    """A single named capability exported by the semantic closure package.

    Each ClosureCapability has a unique identifier, a human-readable name, a detailed
    description of its semantics, a minimum TrustTier required to invoke it, and a
    frozenset of other capability IDs that must be present before this capability can
    be exercised. The dependency mechanism prevents callers from invoking higher-order
    capabilities without first satisfying lower-level prerequisites.

    Instances are frozen and therefore hashable, which allows them to be stored in
    frozensets and used as dictionary keys.
    """

    capability_id: str
    name: str
    description: str
    required_tier: TrustTier
    dependencies: FrozenSet

    def is_available_at_tier(self, tier: TrustTier) -> bool:
        """Check whether this capability is available at the given trust tier.

        The tier comparison is performed using the numeric .value attributes of the
        TrustTier enum members, so that the ordering PROPOSAL < REVIEWED < VERIFIED <
        RUNTIME_WITNESSED < PROOF_BACKED is respected correctly. A capability is
        considered available if the supplied tier is at least as high as the capability's
        own required_tier. This method is used by permission-checking logic to gate
        access to advanced capabilities. It does not check dependency satisfaction —
        for that, use dependency_satisfied() separately.

        Args:
            tier: The caller's current trust tier.

        Returns:
            bool: True if tier.value >= self.required_tier.value.
        """
        if tier is None:
            return False
        return tier.value >= self.required_tier.value

    def dependency_satisfied(self, available: FrozenSet) -> bool:
        """Check whether all dependency capability IDs are present in the available set.

        The dependency set self.dependencies is treated as a set of required precondition
        capability IDs. This method returns True if and only if every element of
        self.dependencies is also a member of the available frozenset. An empty
        dependency set trivially satisfies the condition, so capabilities with no
        declared dependencies are always dependency-satisfied. This method is combined
        with is_available_at_tier() to provide full access-control logic.

        Args:
            available: FrozenSet of currently available capability IDs.

        Returns:
            bool: True if self.dependencies is a subset of available.
        """
        if available is None:
            return len(self.dependencies) == 0
        return self.dependencies <= available

    def to_judgment_tuple(self) -> tuple:
        """Serialise this capability to a plain tuple for embedding in Judgment objects.

        The returned tuple contains the capability_id, name, required tier name, and
        the count of dependencies. This compact representation is sufficient for
        judgment-level bookkeeping without embedding the full description string.
        The tuple format is stable across schema versions since ClosureCapability is
        a leaf-level object not subject to versioned schema changes.

        Returns:
            tuple: A four-element summary tuple.
        """
        return (
            self.capability_id,
            self.name,
            self.required_tier.name,
            len(self.dependencies),
        )

    def capability_key(self) -> str:
        """Compute a short deterministic key for this capability using SHA-256.

        The key is computed by hashing the concatenation of capability_id and name
        using the SHA-256 algorithm and returning the first 16 hexadecimal characters
        of the digest. This produces a compact, collision-resistant identifier that
        can be used as a dictionary key or database row identifier. The key is
        deterministic for the same (capability_id, name) pair, making it stable
        across process restarts. The 16-character prefix provides 64 bits of
        collision resistance, which is sufficient for manifests with at most thousands
        of capabilities.

        Returns:
            str: First 16 hex characters of SHA-256(capability_id + name).
        """
        raw = (self.capability_id + self.name).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return digest[:16]

    def summarize(self) -> str:
        """Produce a concise single-paragraph summary of this capability.

        The summary includes the capability_id, human-readable name, required tier,
        dependency count, and a truncated description. It is intended for display in
        manifest reports and log output where a full dump of all fields would be too
        verbose. The description is truncated at 120 characters and an ellipsis is
        appended if truncation occurs. The method always returns a non-empty string.

        Returns:
            str: A one-line summary string.
        """
        desc = self.description
        if len(desc) > 120:
            desc = desc[:117] + "..."
        dep_str = "none" if not self.dependencies else ", ".join(sorted(self.dependencies))
        return (
            "[{}] {} (tier={}, deps={}) — {}".format(
                self.capability_id,
                self.name,
                self.required_tier.name,
                dep_str,
                desc,
            )
        )


@dataclass(frozen=True)
class ExportedSymbol:
    """A single symbol exported from the semantic closure package.

    ExportedSymbol records the fully qualified module path and symbol name of each
    exported API element, along with the trust tier at which the symbol is considered
    stable. Deprecated symbols are flagged via the deprecated boolean so that tooling
    can emit warnings when they are imported. The frozen dataclass is hashable and
    can be stored in frozensets, making it easy to collect the full export surface
    into an immutable manifest.

    The symbol_kind field records whether the symbol is a 'class', 'function',
    'constant', 'exception', or other Python object kind.
    """

    symbol_name: str
    module_path: str
    symbol_kind: str
    export_tier: TrustTier
    deprecated: bool

    def qualified_name(self) -> str:
        """Return the fully qualified dotted name of this symbol.

        The qualified name is formed by joining module_path and symbol_name with a
        single dot separator. This matches the standard Python import dotted name
        convention and can be used with importlib.import_module() to dynamically
        resolve the symbol at runtime. The method does not verify that the symbol
        actually exists in the module — it only constructs the name string.

        Returns:
            str: 'module_path.symbol_name'.
        """
        return "{}.{}".format(self.module_path, self.symbol_name)

    def is_public(self) -> bool:
        """Determine whether this symbol is part of the public API.

        By Python convention, names beginning with a single underscore are considered
        private and names beginning with double underscores are mangled. This method
        returns False for any symbol whose name begins with an underscore, and True
        for all other symbols. Consumers should check is_public() before including
        a symbol in generated documentation or external API listings.

        Returns:
            bool: True if symbol_name does not start with '_'.
        """
        return not self.symbol_name.startswith("_")

    def deprecation_warning(self) -> str:
        """Return a human-readable deprecation warning for this symbol, if applicable.

        If the deprecated flag is True, this method returns a formatted warning string
        that includes the qualified name and a recommendation to migrate to a non-
        deprecated alternative. If deprecated is False, the method returns an empty
        string so that callers can use the return value directly in conditional
        output logic without needing to check the deprecated flag themselves.
        The warning string is suitable for use with Python's warnings.warn() or for
        inclusion in log messages.

        Returns:
            str: Deprecation warning string, or empty string if not deprecated.
        """
        if not self.deprecated:
            return ""
        return (
            "DeprecationWarning: '{}' is deprecated and may be removed in a future version. "
            "Please migrate to a supported alternative.".format(self.qualified_name())
        )

    def to_judgment_tuple(self) -> tuple:
        """Serialise this symbol to a compact tuple for Judgment embedding.

        The tuple contains the qualified name, symbol kind, tier name, and deprecated
        flag. This is the minimal information required to identify and characterise
        the symbol in a judgment context. The format is stable and does not embed
        the full module path and symbol name separately since qualified_name() already
        combines them.

        Returns:
            tuple: A four-element summary tuple.
        """
        return (
            self.qualified_name(),
            self.symbol_kind,
            self.export_tier.name,
            self.deprecated,
        )

    def symbol_key(self) -> str:
        """Compute a short deterministic key for this symbol using SHA-256.

        The key is derived from the qualified name of the symbol (module_path + '.' +
        symbol_name). The first 16 hexadecimal characters of the SHA-256 digest are
        returned, providing a compact collision-resistant identifier. This key is
        used in manifest entry lookups and database indexing. The key is stable as
        long as the qualified name does not change.

        Returns:
            str: First 16 hex characters of SHA-256(qualified_name()).
        """
        raw = self.qualified_name().encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return digest[:16]


@dataclass(frozen=True)
class ManifestEntry:
    """A single entry in the semantic closure package manifest.

    A ManifestEntry binds an ExportedSymbol to the set of capability IDs that must
    be available for the symbol to be accessible. This allows the manifest to express
    fine-grained access control: some symbols are accessible unconditionally while
    others require specific capabilities to be present. The entry also records the
    TrustTier at which the binding was established and a human-readable description
    of why the binding exists.

    Entries are frozen and hashable, enabling them to be stored in frozensets and
    compared for equality.
    """

    entry_id: str
    symbol: ExportedSymbol
    capability_ids: FrozenSet
    entry_tier: TrustTier
    description: str

    def references_capability(self, cap_id: str) -> bool:
        """Check whether this entry references a specific capability by ID.

        The membership test is performed against self.capability_ids after stripping
        whitespace from the supplied cap_id. None and empty-string inputs return
        False gracefully. This method is used by capability-gating logic to determine
        which entries become inaccessible when a capability is revoked. The check is
        case-sensitive and exact-match only.

        Args:
            cap_id: The capability ID to look for.

        Returns:
            bool: True if cap_id is in self.capability_ids.
        """
        if cap_id is None:
            return False
        stripped = cap_id.strip()
        if not stripped:
            return False
        return stripped in self.capability_ids

    def to_judgment_tuple(self) -> tuple:
        """Serialise this entry to a compact tuple for Judgment embedding.

        The returned tuple includes the entry_id, the symbol's qualified name, the
        count of capability IDs, and the tier name. This is sufficient for identifying
        and characterising an entry in a judgment without embedding the full symbol
        and capability data. The tuple is hashable and can be used as a dictionary key.

        Returns:
            tuple: A four-element summary tuple.
        """
        return (
            self.entry_id,
            self.symbol.qualified_name(),
            len(self.capability_ids),
            self.entry_tier.name,
        )

    def entry_summary(self) -> str:
        """Produce a human-readable summary line for this manifest entry.

        The summary includes the entry_id, the symbol's qualified name and kind, the
        trust tier, and whether the symbol is deprecated. It also lists the capability
        IDs required to access the symbol. The summary is formatted as a single line
        suitable for inclusion in tabular manifest reports. Long capability lists are
        truncated at three entries with a count of remaining entries appended.

        Returns:
            str: A single-line summary of this entry.
        """
        dep_flag = " [DEPRECATED]" if self.symbol.deprecated else ""
        caps = sorted(self.capability_ids)
        if len(caps) > 3:
            cap_str = ", ".join(caps[:3]) + " +{} more".format(len(caps) - 3)
        else:
            cap_str = ", ".join(caps) if caps else "none"
        return (
            "{} | {} ({}{}) | tier={} | caps=[{}] | {}".format(
                self.entry_id,
                self.symbol.qualified_name(),
                self.symbol.symbol_kind,
                dep_flag,
                self.entry_tier.name,
                cap_str,
                self.description[:60],
            )
        )

    def lookup_key(self) -> str:
        """Compute a deterministic lookup key for this entry.

        The lookup key is a SHA-256 hash of the concatenation of entry_id and the
        symbol's qualified name. The first 16 hexadecimal characters are returned.
        This key is stable for a given (entry_id, symbol) pair and can be used to
        index entries in a manifest registry without needing to store full entry
        objects as dictionary keys.

        Returns:
            str: First 16 hex chars of SHA-256(entry_id + qualified_name).
        """
        raw = (self.entry_id + self.symbol.qualified_name()).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return digest[:16]

    def is_accessible(self, available_caps: FrozenSet) -> bool:
        """Determine whether this entry is accessible given a set of available capabilities.

        An entry is accessible if and only if every capability ID in self.capability_ids
        is present in the available_caps frozenset. An entry with an empty capability_ids
        set is always accessible. This method is the primary access-control check used
        by the manifest to gate symbol visibility. It does not check trust tiers — that
        is handled separately by the calling layer.

        Args:
            available_caps: FrozenSet of currently available capability IDs.

        Returns:
            bool: True if self.capability_ids is a subset of available_caps.
        """
        if available_caps is None:
            return len(self.capability_ids) == 0
        return self.capability_ids <= available_caps


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def build_manifest(
    package_name: str = PACKAGE_NAME,
    version: tuple = PACKAGE_VERSION,
    tier: TrustTier = TrustTier.REVIEWED,
) -> SemanticClosureManifest:
    """Build and return a SemanticClosureManifest with the standard capabilities and modules.

    This factory function assembles a SemanticClosureManifest by collecting the keys
    from KNOWN_CAPABILITIES as the capability tuple and converting DEFAULT_EXPORTED_MODULES
    into a frozenset. The caller may override package_name, version, and tier to produce
    manifests for non-standard configurations. The function does not raise — if KNOWN_CAPABILITIES
    is empty, the manifest will have no capabilities but will otherwise be valid.

    Args:
        package_name: The dotted package name for the manifest.
        version: The version tuple (major, minor, patch).
        tier: The TrustTier to associate with the manifest.

    Returns:
        SemanticClosureManifest: A fully populated, immutable manifest.
    """
    caps = tuple(sorted(KNOWN_CAPABILITIES.keys()))
    mods = frozenset(DEFAULT_EXPORTED_MODULES)
    return SemanticClosureManifest(
        package_name=package_name,
        version=version,
        capabilities=caps,
        exported_modules=mods,
        manifest_tier=tier,
    )


def list_exports(manifest: SemanticClosureManifest) -> List[str]:
    """Return a sorted list of all module paths exported by the given manifest.

    The list is derived from manifest.exported_modules, sorted alphabetically, and
    returned as a plain Python list. This function is a convenience wrapper that
    hides the frozenset type of the underlying field from callers who expect a list.
    It does not modify the manifest. An empty list is returned if no modules are exported.

    Args:
        manifest: The SemanticClosureManifest to query.

    Returns:
        List[str]: Sorted list of exported module paths.
    """
    if manifest is None:
        return []
    return sorted(manifest.exported_modules)


def get_version(manifest: SemanticClosureManifest) -> str:
    """Return the version string of the given manifest.

    This is a thin wrapper around manifest.version_str() that provides a module-level
    function interface. It is useful for callers who receive a manifest object through
    a generic Any-typed interface and want to extract the version without importing
    the SemanticClosureManifest class directly.

    Args:
        manifest: The manifest to query.

    Returns:
        str: The version string, e.g. '0.4.1'.
    """
    if manifest is None:
        return "0.0.0"
    return manifest.version_str()


def describe_capability(cap: ClosureCapability) -> str:
    """Return a full human-readable description of a ClosureCapability.

    The description combines the capability's name, ID, required tier, dependency list,
    and full description text into a multi-line string. This function is provided as
    a module-level convenience so that callers with only the cap object available do
    not need to re-implement the formatting logic. The output is suitable for use
    in manifest reports and CLI help text.

    Args:
        cap: The ClosureCapability to describe.

    Returns:
        str: Multi-line description string.
    """
    if cap is None:
        return "(null capability)"
    dep_list = ", ".join(sorted(cap.dependencies)) if cap.dependencies else "none"
    tier_label = TIER_LABELS.get(cap.required_tier, cap.required_tier.name)
    return (
        "Capability: {name}\n"
        "  ID          : {cid}\n"
        "  Tier        : {tier}\n"
        "  Dependencies: {deps}\n"
        "  Description : {desc}\n"
        "  Key         : {key}\n"
    ).format(
        name=cap.name,
        cid=cap.capability_id,
        tier=tier_label,
        deps=dep_list,
        desc=cap.description,
        key=cap.capability_key(),
    )


# ---------------------------------------------------------------------------
# __main__ demonstration block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("jugeo.generation.semantic_closure.manifest — demo")
    print("=" * 70)

    # 1. Build the standard manifest
    manifest = build_manifest()
    print("\n[1] Standard manifest built:")
    print("    package_name   :", manifest.package_name)
    print("    version_str    :", manifest.version_str())
    print("    tier           :", manifest.manifest_tier.name)
    print("    capabilities   :", len(manifest.capabilities))
    print("    exported_mods  :", len(manifest.exported_modules))

    # 2. Check has_capability for known and unknown keys
    print("\n[2] Capability checks:")
    for cap_id in ("closure_check", "global_gluing", "nonexistent_cap", "", "  closure_check  "):
        print("    has_capability({!r}) -> {}".format(cap_id, manifest.has_capability(cap_id)))

    # 3. Check exports_module
    print("\n[3] Module export checks:")
    for mod in (DEFAULT_EXPORTED_MODULES[0], "jugeo.unknown.module", ""):
        print("    exports_module({!r}) -> {}".format(mod, manifest.exports_module(mod)))

    # 4. to_judgment_tuple
    jt = manifest.to_judgment_tuple()
    print("\n[4] Judgment tuple:", jt)

    # 5. manifest_summary
    print("\n[5] Manifest summary:")
    print(manifest.manifest_summary())

    # 6. Build ClosureCapability instances
    print("\n[6] ClosureCapability instances:")
    cap_a = ClosureCapability(
        capability_id="closure_check",
        name="Closure Check",
        description=KNOWN_CAPABILITIES["closure_check"],
        required_tier=TrustTier.REVIEWED,
        dependencies=frozenset(),
    )
    cap_b = ClosureCapability(
        capability_id="global_gluing",
        name="Global Gluing",
        description=KNOWN_CAPABILITIES["global_gluing"],
        required_tier=TrustTier.VERIFIED,
        dependencies=frozenset(["closure_check", "local_verification"]),
    )
    for cap in (cap_a, cap_b):
        print("   ", cap.summarize())
        print("    key:", cap.capability_key())
        for t in TrustTier:
            print("      is_available_at_tier({}) -> {}".format(t.name, cap.is_available_at_tier(t)))

    # 7. dependency_satisfied checks
    print("\n[7] dependency_satisfied checks:")
    available = frozenset(["closure_check", "local_verification"])
    print("    cap_a.dependency_satisfied({}) -> {}".format(available, cap_a.dependency_satisfied(available)))
    print("    cap_b.dependency_satisfied({}) -> {}".format(available, cap_b.dependency_satisfied(available)))
    missing = frozenset(["closure_check"])
    print("    cap_b.dependency_satisfied({}) -> {}".format(missing, cap_b.dependency_satisfied(missing)))

    # 8. ExportedSymbol instances
    print("\n[8] ExportedSymbol instances:")
    sym_pub = ExportedSymbol(
        symbol_name="SemanticClosureManifest",
        module_path="jugeo.generation.semantic_closure.manifest",
        symbol_kind="class",
        export_tier=TrustTier.REVIEWED,
        deprecated=False,
    )
    sym_dep = ExportedSymbol(
        symbol_name="legacy_closure_check",
        module_path="jugeo.generation.semantic_closure.manifest",
        symbol_kind="function",
        export_tier=TrustTier.PROPOSAL,
        deprecated=True,
    )
    for sym in (sym_pub, sym_dep):
        print("    qualified_name :", sym.qualified_name())
        print("    is_public      :", sym.is_public())
        print("    symbol_key     :", sym.symbol_key())
        print("    deprecation_warning:", repr(sym.deprecation_warning()))
        print("    to_judgment_tuple:", sym.to_judgment_tuple())
        print()

    # 9. ManifestEntry instances
    print("\n[9] ManifestEntry instances:")
    entry_1 = ManifestEntry(
        entry_id="entry-001",
        symbol=sym_pub,
        capability_ids=frozenset(["closure_check"]),
        entry_tier=TrustTier.REVIEWED,
        description="Primary manifest class for semantic closure.",
    )
    entry_2 = ManifestEntry(
        entry_id="entry-002",
        symbol=sym_dep,
        capability_ids=frozenset(),
        entry_tier=TrustTier.PROPOSAL,
        description="Deprecated legacy function, kept for backwards compat.",
    )
    print("    entry_1 summary:", entry_1.entry_summary())
    print("    entry_1 lookup_key:", entry_1.lookup_key())
    print("    entry_1 references_capability('closure_check'):", entry_1.references_capability("closure_check"))
    print("    entry_1 references_capability('global_gluing'):", entry_1.references_capability("global_gluing"))
    print("    entry_1 is_accessible(frozenset(['closure_check'])):",
          entry_1.is_accessible(frozenset(["closure_check"])))
    print("    entry_1 is_accessible(frozenset()):",
          entry_1.is_accessible(frozenset()))
    print("    entry_2 summary:", entry_2.entry_summary())
    print("    entry_2 is_accessible(frozenset()):", entry_2.is_accessible(frozenset()))

    # 10. list_exports and get_version
    print("\n[10] list_exports:")
    for mod in list_exports(manifest):
        print("    -", mod)
    print("\n[11] get_version:", get_version(manifest))

    # 12. describe_capability
    print("\n[12] describe_capability:")
    print(describe_capability(cap_a))
    print(describe_capability(cap_b))

    # 13. Build manifest with custom parameters
    print("\n[13] Custom manifest (PROOF_BACKED tier):")
    custom = build_manifest(
        package_name="jugeo.generation.semantic_closure.custom",
        version=(1, 0, 0),
        tier=TrustTier.PROOF_BACKED,
    )
    print("    version_str:", custom.version_str())
    print("    tier:", TIER_LABELS[custom.manifest_tier])
    print("    to_judgment_tuple:", custom.to_judgment_tuple())

    print("\n[done]")
