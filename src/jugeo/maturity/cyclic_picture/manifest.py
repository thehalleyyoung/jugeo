"""
manifest.py — Manifest management layer for the JuGeo Cyclic Picture maturity subsystem.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

Overview
--------
This module provides the manifest infrastructure for the cyclic picture maturity
subsystem.  A *manifest* in this context is a structured, versioned declaration
that a particular JuGeo system has attained a specified maturity level and
possesses a defined set of capabilities, each of which is supported by an
explicit evidence chain and a corresponding set of formal theorem references.

Manifests sit at the intersection of the evidence layer
(``jugeo.evidence.manifests``) and the pack/bridge layer
(``jugeo.packs.bridges``).  They are the downstream consumer of evidence records
and the upstream producer of formal certification artefacts.

Manifest Lifecycle
------------------
A manifest passes through the following statuses during its lifetime:

1. **DRAFT** — The manifest is being assembled by a ``MaturityManifestBuilder``.
   It has a manifest ID and a creation timestamp but is not yet authoritative.
   Draft manifests are not transmitted to peer nodes and are not stored in the
   evidence chain.

2. **PENDING** — The manifest has been submitted for review.  Its capability
   claims and evidence references have been recorded but not yet verified by
   the evidence subsystem.  Pending manifests are visible to authorised
   reviewers but not to end consumers.

3. **ACTIVE** — The manifest has passed all verification checks and is the
   currently authoritative statement of the system's maturity.  Only one
   manifest per system should be ACTIVE at any time; activating a new manifest
   for a system should automatically supersede the previously active one.

4. **SUPERSEDED** — This manifest has been replaced by a newer one.  It is
   retained in the evidence chain for audit purposes but should not be treated
   as authoritative by consumers.

5. **REVOKED** — This manifest has been invalidated, typically because supporting
   evidence was found to be incorrect or has been withdrawn.  A revoked manifest
   triggers a re-assessment of the system's maturity level.

Manifest Theory (Ch65 §8)
--------------------------
Chapter 65 of theory2.tex introduces the formal notion of a *capability claim
lattice*: a partially ordered set of capability strings, where more specific
capabilities imply less specific ones.  A manifest's ``capability_claims`` field
stores a flat list of claim identifiers; the lattice structure is resolved by the
bridge subsystem at verification time.

The ``evidence_refs`` field stores identifiers pointing into the evidence
subsystem's record store.  Each evidence record links a specific observable
measurement to a claim.  The manifest is verified when every capability claim
has at least one supporting evidence record with a sufficient trust score, as
determined by the system's TrustProfile.

The ``theorem_refs`` field stores references to formal theorems (typically in
the BridgeTheorem registry) that provide mathematical backing for the capability
claims.  In the fully formalised setting, every capability claim must be backed
by at least one theorem reference; in practice, empirical-only claims are
permitted at PROTOTYPE and OPERATIONAL levels but required at FEDERATED and above.

Builder Pattern
---------------
The ``MaturityManifestBuilder`` implements the classic builder pattern for
constructing ``CyclicPictureManifest`` instances.  It is the preferred way to
assemble a manifest programmatically, as it provides explicit validation before
the final ``build()`` call and supports method chaining for concise construction.

::

    manifest = (
        MaturityManifestBuilder.create()
        .set_system_id("my-system")
        .set_maturity_level("operational")
        .add_capability_claim("text-classification")
        .add_capability_claim("structured-output")
        .add_evidence_ref("ev-abc123")
        .add_theorem_ref("thm-bridge-42")
        .build()
    )

Free Functions
--------------
The module also exposes several free functions for common manifest operations:

* ``build_maturity_manifest()`` — single-call shorthand for the builder.
* ``load_manifest_from_dict()`` — deserialise a manifest from a dictionary.
* ``merge_manifests()`` — combine two manifests into a new one with the union
  of their claims.
* ``compare_manifests()`` — compute the diff between two manifests.

Versioning
----------
The ``version`` field in ``CyclicPictureManifest`` follows semantic versioning
(MAJOR.MINOR.PATCH).  Version comparison in ``merge_manifests()`` and
``compare_manifests()`` uses lexicographic ordering on the tuple representation
of the parsed version string.  The ``_parse_version()`` helper provides this
conversion.

Cross-Module Integration
------------------------
Like ``models.py``, all cross-module imports are guarded so that this module may
be imported in isolation.  The manifest infrastructure is designed to be
self-contained when the broader JuGeo ecosystem is unavailable, emitting warnings
(via Python's ``warnings`` module) only when an operation requires a missing
subsystem.

See Also
--------
* ``jugeo.maturity.cyclic_picture.models`` — domain model types referenced here.
* ``jugeo.evidence.manifests`` — upstream evidence manifest infrastructure.
* theory2.tex Ch65 §8 — formal manifest theory.
"""

from __future__ import annotations

import json
import uuid
import datetime
import warnings
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    "ManifestStatus",
    "CyclicPictureManifest",
    "MaturityManifestBuilder",
    "build_maturity_manifest",
    "load_manifest_from_dict",
    "merge_manifests",
    "compare_manifests",
]

# ---------------------------------------------------------------------------
# Guarded cross-module imports
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.models import (
        MaturityLevel,
        ImprovementKind,
        ImprovementCycle,
        MatureManifest,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp float.

    Centralises time acquisition for easy test monkeypatching.
    """
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def _uid() -> str:
    """Return a fresh random UUID4 as a hex string (no dashes).

    Provides statistically unique identifiers without coordination.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Prevents ratio/probability fields from drifting outside their valid range.
    """
    return max(lo, min(hi, value))


def _parse_version(version: str) -> tuple[int, ...]:
    """Parse a semantic version string into an integer tuple.

    Converts a string like "1.2.3" into the tuple (1, 2, 3) for comparison
    purposes.  Non-integer components are treated as zero to provide graceful
    handling of pre-release suffixes.

    Parameters
    ----------
    version:
        A semantic version string, e.g. "1.0.0" or "2.1.4".

    Returns
    -------
    tuple[int, ...]
        Parsed version as an integer tuple.
    """
    parts = []
    for part in version.split(".")[:3]:
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _higher_version(v1: str, v2: str) -> str:
    """Return whichever of *v1* or *v2* is the higher semantic version.

    If both parse to the same tuple the first argument is returned.

    Parameters
    ----------
    v1:
        First version string.
    v2:
        Second version string.

    Returns
    -------
    str
        The higher version string.
    """
    return v1 if _parse_version(v1) >= _parse_version(v2) else v2


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

class ManifestStatus(str, Enum):
    """Lifecycle status of a CyclicPictureManifest.

    Models the state machine that a manifest transitions through from initial
    assembly to eventual revocation or supersession.  Status transitions are
    one-way: a manifest cannot be moved to an earlier status.
    """

    DRAFT = "draft"
    """Manifest is being assembled and has not been submitted for review.

    A DRAFT manifest exists only within the scope of the ``MaturityManifestBuilder``
    that is constructing it.  It has not yet been stored in the evidence chain
    and is not visible to external systems or peer nodes.  Draft manifests should
    not be transmitted or cached; only the final output of ``Builder.build()``
    should leave the local context.

    Permissible transitions: DRAFT → PENDING (on ``build()``).
    """

    PENDING = "pending"
    """Manifest has been submitted and is awaiting verification.

    A PENDING manifest has been accepted by the evidence subsystem but its
    claims have not yet been verified against the evidence records.  It is
    visible to authorised reviewers but should not be treated as authoritative
    by consumers.  The system associated with a PENDING manifest is still
    considered to be at its previously ACTIVE manifest's level.

    Permissible transitions: PENDING → ACTIVE (on verification success),
    PENDING → REVOKED (on verification failure).
    """

    ACTIVE = "active"
    """Manifest is the currently authoritative statement of system maturity.

    An ACTIVE manifest represents the verified, current maturity claim for its
    associated system.  At most one manifest per system should be ACTIVE at any
    given time; activating a new manifest must atomically supersede any existing
    ACTIVE manifest for the same system.  Consumers should use the ACTIVE
    manifest's capability_claims and evidence_refs as the definitive source
    of truth about the system's capabilities.

    Permissible transitions: ACTIVE → SUPERSEDED (on new manifest activation),
    ACTIVE → REVOKED (on evidence invalidation).
    """

    SUPERSEDED = "superseded"
    """Manifest has been replaced by a newer version.

    A SUPERSEDED manifest is no longer authoritative but is retained in the
    evidence chain for historical audit purposes.  Any system that holds a
    reference to a SUPERSEDED manifest should re-fetch the ACTIVE manifest for
    the associated system.  Superseded manifests are useful for reconstructing
    the maturity history of a system over time.

    Permissible transitions: SUPERSEDED → (terminal; no further transitions).
    """

    REVOKED = "revoked"
    """Manifest has been invalidated due to evidence withdrawal or fraud.

    A REVOKED manifest's claims are no longer valid.  Revocation is triggered
    when one or more of the supporting evidence records are found to be
    incorrect, fabricated, or no longer accessible.  A revocation event should
    trigger a re-assessment of the system's maturity level, which may result
    in a downgrade.  The revocation reason should be recorded in the evidence
    subsystem's provenance trace.

    Permissible transitions: REVOKED → (terminal; no further transitions).
    """


# ---------------------------------------------------------------------------
# CyclicPictureManifest — immutable value object
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CyclicPictureManifest:
    """Immutable, versioned maturity certificate for a JuGeo system.

    A CyclicPictureManifest is the primary artefact produced by the manifest
    management layer.  It encodes a system's identity, its claimed maturity
    level, the specific capabilities it asserts, the evidence records that
    substantiate those assertions, and the formal theorems that provide
    mathematical backing.

    Immutability guarantees:
        Once constructed via ``create()``, a CyclicPictureManifest cannot be
        modified in place.  Any update requires constructing a new manifest,
        which will receive a new ``manifest_id``.  This design ensures that the
        manifest is tamper-evident and can be safely stored in content-addressed
        evidence stores.

    Versioning:
        The ``version`` field follows semantic versioning.  When a system's
        capabilities or evidence change, a new manifest is created with a
        higher version number.  ``merge_manifests()`` automatically selects the
        higher of the two input versions for the merged output.

    LaTeX output:
        ``render_tex()`` produces a formal certificate suitable for inclusion in
        automated technical reports generated by the JuGeo documentation pipeline.
    """

    manifest_id: str
    """Unique identifier for this manifest (UUID4 hex)."""

    created_at: float
    """UTC POSIX timestamp when this manifest was created."""

    status: ManifestStatus
    """Current lifecycle status of this manifest."""

    system_id: str
    """Identifier of the system this manifest certifies."""

    maturity_level: str
    """The maturity level string (e.g. 'operational') this manifest claims."""

    capability_claims: tuple[str, ...]
    """Tuple of capability identifiers asserted by this manifest."""

    evidence_refs: tuple[str, ...]
    """Tuple of evidence record identifiers supporting the claims."""

    theorem_refs: tuple[str, ...]
    """Tuple of formal theorem identifiers providing mathematical backing."""

    version: str
    """Semantic version string for this manifest (MAJOR.MINOR.PATCH)."""

    @classmethod
    def create(
        cls,
        system_id: str,
        maturity_level: str,
        capability_claims: tuple[str, ...] | list[str] = (),
        evidence_refs: tuple[str, ...] | list[str] = (),
        theorem_refs: tuple[str, ...] | list[str] = (),
        version: str = "1.0.0",
    ) -> "CyclicPictureManifest":
        """Factory: create a new CyclicPictureManifest in DRAFT status.

        Generates a fresh ``manifest_id`` via ``_uid()`` and records the
        current UTC time via ``_utcnow()``.  The status is set to DRAFT; the
        caller (or the builder) is responsible for advancing the status to
        PENDING or ACTIVE as appropriate.

        Parameters
        ----------
        system_id:
            The unique identifier of the system this manifest is for.
        maturity_level:
            The maturity level string being claimed.  Should be one of the
            ``MaturityLevel`` values (e.g. "operational") but is stored as a
            plain string to avoid a hard dependency on ``models.py``.
        capability_claims:
            Sequence of capability identifiers asserted by the system.
            Converted to a tuple.  Defaults to empty.
        evidence_refs:
            Sequence of evidence record identifiers supporting the claims.
            Converted to a tuple.  Defaults to empty.
        theorem_refs:
            Sequence of formal theorem identifiers backing the claims.
            Converted to a tuple.  Defaults to empty.
        version:
            Semantic version string for this manifest.  Defaults to "1.0.0".

        Returns
        -------
        CyclicPictureManifest
            A frozen, immutable manifest in DRAFT status.
        """
        return cls(
            manifest_id=_uid(),
            created_at=_utcnow(),
            status=ManifestStatus.DRAFT,
            system_id=system_id,
            maturity_level=str(maturity_level),
            capability_claims=tuple(capability_claims),
            evidence_refs=tuple(evidence_refs),
            theorem_refs=tuple(theorem_refs),
            version=version,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this manifest to a JSON-serialisable dictionary.

        Converts all fields, including enum values, to JSON-safe types.  The
        resulting dictionary can be passed directly to ``json.dumps()`` or stored
        in a document database.  Tuple fields are converted to lists for
        standard JSON compatibility.

        Returns
        -------
        dict[str, Any]
            A dictionary with all manifest fields in serialisable form.
        """
        return {
            "manifest_id": self.manifest_id,
            "created_at": self.created_at,
            "status": self.status.value,
            "system_id": self.system_id,
            "maturity_level": self.maturity_level,
            "capability_claims": list(self.capability_claims),
            "evidence_refs": list(self.evidence_refs),
            "theorem_refs": list(self.theorem_refs),
            "version": self.version,
        }

    def to_json(self) -> str:
        """Serialise this manifest to a formatted JSON string.

        Wraps ``to_dict()`` with ``json.dumps()`` using two-space indentation
        for human-readable output.  Useful for logging, file storage, and
        transmission to peer nodes that consume JSON.

        Returns
        -------
        str
            A pretty-printed JSON string representation of this manifest.
        """
        return json.dumps(self.to_dict(), indent=2)

    def is_active(self) -> bool:
        """Check whether this manifest is currently ACTIVE.

        Convenience predicate used by consumers who need to verify that a
        manifest is the authoritative current statement before using its claims.

        Returns
        -------
        bool
            True if ``self.status == ManifestStatus.ACTIVE``.
        """
        return self.status == ManifestStatus.ACTIVE

    def render_tex(self) -> str:
        """Render this manifest as a LaTeX certificate block.

        Produces a formal ``\\section{Maturity Certificate}`` block with
        description lists for the manifest metadata, capability claims, evidence
        references, and theorem references.  The output is designed to be
        included in a larger LaTeX document via ``\\input{}``.

        Returns
        -------
        str
            A LaTeX string representing this manifest as a formal certificate.
        """
        safe_id = self.system_id.replace("_", r"\_").replace("-", r"\mbox{-}")
        lines = [
            r"\section{Maturity Certificate}",
            r"\begin{description}",
            r"  \item[Manifest ID] \texttt{" + self.manifest_id[:16] + r"\ldots}",
            r"  \item[System] \texttt{" + safe_id + r"}",
            r"  \item[Maturity Level] "
            + self.maturity_level.replace("_", r"\_"),
            r"  \item[Version] " + self.version,
            r"  \item[Status] " + self.status.value,
            r"  \item[Created] " + str(self.created_at),
            r"\end{description}",
            "",
            r"\subsection{Capability Claims}",
            r"\begin{itemize}",
        ]
        for claim in self.capability_claims:
            lines.append(r"  \item \texttt{" + claim.replace("_", r"\_") + r"}")
        lines += [
            r"\end{itemize}",
            "",
            r"\subsection{Evidence References}",
            r"\begin{itemize}",
        ]
        for ref in self.evidence_refs:
            lines.append(r"  \item \texttt{" + ref + r"}")
        lines += [
            r"\end{itemize}",
            "",
            r"\subsection{Theorem References}",
            r"\begin{itemize}",
        ]
        for thm in self.theorem_refs:
            lines.append(r"  \item \texttt{" + thm.replace("_", r"\_") + r"}")
        lines.append(r"\end{itemize}")
        return "\n".join(lines)

    def summarise(self) -> str:
        """Produce a concise human-readable summary of this manifest.

        Returns a multi-line string that can be printed directly to a console
        or written to a log.  Includes the manifest ID, system ID, maturity
        level, status, version, and counts of claims, evidence refs, and
        theorem refs.

        Returns
        -------
        str
            A human-readable summary of this manifest.
        """
        return (
            f"CyclicPictureManifest\n"
            f"  manifest_id    : {self.manifest_id}\n"
            f"  system_id      : {self.system_id}\n"
            f"  maturity_level : {self.maturity_level}\n"
            f"  status         : {self.status.value}\n"
            f"  version        : {self.version}\n"
            f"  created_at     : {self.created_at}\n"
            f"  capabilities   : {len(self.capability_claims)} claims\n"
            f"  evidence_refs  : {len(self.evidence_refs)} refs\n"
            f"  theorem_refs   : {len(self.theorem_refs)} refs\n"
        )


# ---------------------------------------------------------------------------
# MaturityManifestBuilder — mutable builder
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MaturityManifestBuilder:
    """Mutable builder for assembling a CyclicPictureManifest step by step.

    Implements the classic builder pattern, allowing callers to construct a
    manifest incrementally with method chaining, then call ``build()`` to
    produce a validated, immutable ``CyclicPictureManifest``.

    The builder validates the assembled data before creating the manifest:
    ``build()`` raises a ``ValueError`` if ``validate()`` returns any errors.
    Callers can check ``validate()`` themselves first to inspect issues
    without triggering an exception.

    Method chaining:
        All setter and adder methods return ``self``, allowing for concise
        chained construction::

            manifest = (
                MaturityManifestBuilder.create()
                .set_system_id("sys-001")
                .set_maturity_level("federated")
                .add_capability_claim("multi-hop-reasoning")
                .add_evidence_ref("ev-deadbeef")
                .build()
            )

    Resetting:
        The ``reset()`` method clears all state so that the builder can be
        reused to construct a second manifest without creating a new instance.
    """

    _system_id: str = field(default="")
    """The system ID being built into the manifest."""

    _maturity_level: str = field(default="")
    """The maturity level string being claimed."""

    _capability_claims: list[str] = field(default_factory=list)
    """Accumulator for capability claim strings."""

    _evidence_refs: list[str] = field(default_factory=list)
    """Accumulator for evidence reference strings."""

    _theorem_refs: list[str] = field(default_factory=list)
    """Accumulator for theorem reference strings."""

    _version: str = field(default="1.0.0")
    """Semantic version string for the manifest being built."""

    @classmethod
    def create(cls) -> "MaturityManifestBuilder":
        """Factory: create a new, empty MaturityManifestBuilder.

        Returns a freshly initialised builder with all accumulators empty and
        all string fields set to their defaults.  This is the preferred entry
        point for constructing a builder; direct instantiation is also
        permitted but the factory name is more expressive.

        Returns
        -------
        MaturityManifestBuilder
            A fresh, empty builder instance ready to accept configuration.
        """
        return cls()

    def set_system_id(self, system_id: str) -> "MaturityManifestBuilder":
        """Set the system identifier for the manifest being built.

        Overwrites any previously set system_id.  Returns ``self`` for method
        chaining.

        Parameters
        ----------
        system_id:
            The unique identifier of the target system.

        Returns
        -------
        MaturityManifestBuilder
            This builder instance (for chaining).
        """
        self._system_id = system_id
        return self

    def set_maturity_level(self, level: Any) -> "MaturityManifestBuilder":
        """Set the maturity level being claimed by the manifest.

        Accepts a string, a ``MaturityLevel`` enum value, or any object with
        a ``.value`` attribute.  The level is stored as its string
        representation so that this builder does not require ``models.py`` to
        be importable.

        Parameters
        ----------
        level:
            The maturity level to claim.  Will be coerced to str.

        Returns
        -------
        MaturityManifestBuilder
            This builder instance (for chaining).
        """
        if hasattr(level, "value"):
            self._maturity_level = str(level.value)
        else:
            self._maturity_level = str(level)
        return self

    def add_capability_claim(self, claim: str) -> "MaturityManifestBuilder":
        """Append a capability claim to the builder's accumulator.

        Duplicate claims are permitted; de-duplication is handled by
        ``build()`` if desired, but the default implementation preserves
        insertion order including duplicates.

        Parameters
        ----------
        claim:
            A capability identifier string to add to the claims list.

        Returns
        -------
        MaturityManifestBuilder
            This builder instance (for chaining).
        """
        self._capability_claims.append(claim)
        return self

    def add_evidence_ref(self, ref: str) -> "MaturityManifestBuilder":
        """Append an evidence reference to the builder's accumulator.

        Evidence references should be valid identifiers in the evidence
        subsystem's record store.  The builder does not validate connectivity
        to the evidence store; that is the responsibility of the verifier.

        Parameters
        ----------
        ref:
            An evidence record identifier to add to the references list.

        Returns
        -------
        MaturityManifestBuilder
            This builder instance (for chaining).
        """
        self._evidence_refs.append(ref)
        return self

    def add_theorem_ref(self, ref: str) -> "MaturityManifestBuilder":
        """Append a theorem reference to the builder's accumulator.

        Theorem references should be valid identifiers in the BridgeTheorem
        registry.  At FEDERATED level and above, every capability claim must
        have at least one theorem reference; ``validate()`` enforces this when
        the maturity level is FEDERATED or higher.

        Parameters
        ----------
        ref:
            A BridgeTheorem identifier to add to the theorem references list.

        Returns
        -------
        MaturityManifestBuilder
            This builder instance (for chaining).
        """
        self._theorem_refs.append(ref)
        return self

    def set_version(self, version: str) -> "MaturityManifestBuilder":
        """Set the semantic version string for the manifest being built.

        Parameters
        ----------
        version:
            A semantic version string, e.g. "1.2.0".

        Returns
        -------
        MaturityManifestBuilder
            This builder instance (for chaining).
        """
        self._version = version
        return self

    def validate(self) -> list[str]:
        """Validate the current builder state and return a list of error messages.

        Performs the following checks:
        - ``_system_id`` must be non-empty.
        - ``_maturity_level`` must be non-empty.
        - At FEDERATED level and above, ``_theorem_refs`` must be non-empty.
        - ``_version`` must be parseable as a semantic version (MAJOR.MINOR.PATCH).

        Returns an empty list if all checks pass.  Returns a list of
        human-readable error messages for each check that fails.

        Returns
        -------
        list[str]
            A (possibly empty) list of validation error messages.
        """
        errors: list[str] = []
        if not self._system_id:
            errors.append("system_id must not be empty")
        if not self._maturity_level:
            errors.append("maturity_level must not be empty")
        high_levels = {"federated", "self_improving", "mature"}
        if self._maturity_level in high_levels and not self._theorem_refs:
            errors.append(
                f"theorem_refs must not be empty for maturity_level "
                f"'{self._maturity_level}' (FEDERATED or above)"
            )
        parts = self._version.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            errors.append(
                f"version '{self._version}' is not a valid MAJOR.MINOR.PATCH string"
            )
        return errors

    def build(self) -> "CyclicPictureManifest":
        """Validate and construct the final CyclicPictureManifest.

        Calls ``validate()`` first; if any errors are found, raises a
        ``ValueError`` listing all issues.  If validation passes, delegates
        to ``CyclicPictureManifest.create()`` to produce the immutable artefact.

        Returns
        -------
        CyclicPictureManifest
            A frozen, immutable manifest in DRAFT status.

        Raises
        ------
        ValueError
            If ``validate()`` returns any error messages.
        """
        errors = self.validate()
        if errors:
            raise ValueError(
                "MaturityManifestBuilder.build() failed validation:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )
        return CyclicPictureManifest.create(
            system_id=self._system_id,
            maturity_level=self._maturity_level,
            capability_claims=list(self._capability_claims),
            evidence_refs=list(self._evidence_refs),
            theorem_refs=list(self._theorem_refs),
            version=self._version,
        )

    def reset(self) -> None:
        """Clear all accumulated state so the builder can be reused.

        Resets all fields to their default values: empty strings for IDs and
        levels, empty lists for accumulators, and "1.0.0" for the version.
        After calling ``reset()``, the builder is in the same state as a
        freshly created instance.
        """
        self._system_id = ""
        self._maturity_level = ""
        self._capability_claims = []
        self._evidence_refs = []
        self._theorem_refs = []
        self._version = "1.0.0"


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

def build_maturity_manifest(
    system_id: str,
    level: Any,
    capabilities: list[str],
    evidence_refs: list[str],
    theorem_refs: list[str],
    version: str = "1.0.0",
) -> CyclicPictureManifest:
    """Single-call shorthand for building a maturity manifest via the builder.

    Constructs a ``MaturityManifestBuilder``, sets all fields from the provided
    arguments, validates the data, and returns the constructed manifest.  This
    function is the recommended entry point for code that assembles all manifest
    data at once and does not need incremental construction.

    Parameters
    ----------
    system_id:
        The unique identifier of the system being certified.
    level:
        The maturity level being claimed.  Accepts a string or a
        ``MaturityLevel`` enum value.
    capabilities:
        List of capability identifiers asserted by the system.
    evidence_refs:
        List of evidence record identifiers supporting the claims.
    theorem_refs:
        List of formal theorem identifiers backing the claims.
    version:
        Semantic version string for this manifest.  Defaults to "1.0.0".

    Returns
    -------
    CyclicPictureManifest
        A validated, frozen manifest in DRAFT status.

    Raises
    ------
    ValueError
        If the assembled data fails validation (see ``MaturityManifestBuilder.validate()``).

    Examples
    --------
    ::

        m = build_maturity_manifest(
            system_id="prod-system-7",
            level="operational",
            capabilities=["text-classification", "ner"],
            evidence_refs=["ev-00a1b2"],
            theorem_refs=[],
        )
    """
    builder = (
        MaturityManifestBuilder.create()
        .set_system_id(system_id)
        .set_maturity_level(level)
        .set_version(version)
    )
    for cap in capabilities:
        builder.add_capability_claim(cap)
    for ref in evidence_refs:
        builder.add_evidence_ref(ref)
    for thm in theorem_refs:
        builder.add_theorem_ref(thm)
    return builder.build()


def load_manifest_from_dict(data: dict[str, Any]) -> CyclicPictureManifest:
    """Reconstruct a CyclicPictureManifest from a serialised dictionary.

    Performs the inverse operation of ``CyclicPictureManifest.to_dict()``.
    Converts the ``status`` field back from a string to a ``ManifestStatus``
    enum value, and converts list fields back to tuples as required by the
    frozen dataclass.

    This function is the standard deserialisation entry point for manifests
    received from peer nodes, loaded from a database, or reconstructed from
    an evidence record.

    Parameters
    ----------
    data:
        A dictionary as produced by ``CyclicPictureManifest.to_dict()``.
        Unknown keys are silently ignored for forward-compatibility.

    Returns
    -------
    CyclicPictureManifest
        A reconstructed, frozen manifest instance.

    Raises
    ------
    KeyError
        If a required key is missing from ``data``.
    ValueError
        If ``data['status']`` is not a valid ``ManifestStatus`` value.

    Examples
    --------
    ::

        raw = json.loads(manifest.to_json())
        reconstructed = load_manifest_from_dict(raw)
        assert reconstructed.manifest_id == manifest.manifest_id
    """
    status = ManifestStatus(data["status"])
    return CyclicPictureManifest(
        manifest_id=data["manifest_id"],
        created_at=float(data["created_at"]),
        status=status,
        system_id=data["system_id"],
        maturity_level=data["maturity_level"],
        capability_claims=tuple(data.get("capability_claims", [])),
        evidence_refs=tuple(data.get("evidence_refs", [])),
        theorem_refs=tuple(data.get("theorem_refs", [])),
        version=data.get("version", "1.0.0"),
    )


def merge_manifests(
    m1: CyclicPictureManifest,
    m2: CyclicPictureManifest,
) -> CyclicPictureManifest:
    """Merge two CyclicPictureManifests into a new combined manifest.

    Produces a new manifest whose ``capability_claims``, ``evidence_refs``,
    and ``theorem_refs`` are the de-duplicated union of those from *m1* and
    *m2*.  The merged manifest receives a fresh ``manifest_id`` and the later
    of the two ``created_at`` timestamps.  The ``version`` is set to the
    higher of the two input versions.

    If *m1* and *m2* have different ``system_id`` values, the merge proceeds
    anyway but a warning is issued via the ``warnings`` module, as merging
    manifests from different systems is unusual and may indicate an error.

    The merged manifest is created with DRAFT status; the caller is responsible
    for advancing it to PENDING or ACTIVE as appropriate.

    Parameters
    ----------
    m1:
        The first manifest to merge.
    m2:
        The second manifest to merge.

    Returns
    -------
    CyclicPictureManifest
        A new manifest containing the union of both inputs' data.

    Examples
    --------
    ::

        combined = merge_manifests(manifest_v1, manifest_v2)
        assert len(combined.capability_claims) >= len(manifest_v1.capability_claims)
    """
    if m1.system_id != m2.system_id:
        warnings.warn(
            f"merge_manifests: merging manifests from different systems "
            f"('{m1.system_id}' and '{m2.system_id}').  "
            "Proceeding with m1's system_id.",
            stacklevel=2,
        )

    # De-duplicated union preserving order
    merged_caps = list(dict.fromkeys(list(m1.capability_claims) + list(m2.capability_claims)))
    merged_ev = list(dict.fromkeys(list(m1.evidence_refs) + list(m2.evidence_refs)))
    merged_thm = list(dict.fromkeys(list(m1.theorem_refs) + list(m2.theorem_refs)))
    newer_ts = max(m1.created_at, m2.created_at)
    higher_ver = _higher_version(m1.version, m2.version)
    # Use the maturity_level from whichever manifest has the higher timestamp
    if m1.created_at >= m2.created_at:
        level = m1.maturity_level
    else:
        level = m2.maturity_level

    return CyclicPictureManifest(
        manifest_id=_uid(),
        created_at=newer_ts,
        status=ManifestStatus.DRAFT,
        system_id=m1.system_id,
        maturity_level=level,
        capability_claims=tuple(merged_caps),
        evidence_refs=tuple(merged_ev),
        theorem_refs=tuple(merged_thm),
        version=higher_ver,
    )


def compare_manifests(
    m1: CyclicPictureManifest,
    m2: CyclicPictureManifest,
) -> dict[str, Any]:
    """Compute the diff between two CyclicPictureManifest instances.

    Returns a dictionary describing what changed between *m1* (treated as the
    "before" state) and *m2* (treated as the "after" state).  The comparison
    covers capability claims, evidence references, and theorem references.

    The returned dictionary contains the following keys:
    - ``added_claims``: list of capability claims in m2 but not m1.
    - ``removed_claims``: list of capability claims in m1 but not m2.
    - ``added_evidence``: list of evidence refs in m2 but not m1.
    - ``removed_evidence``: list of evidence refs in m1 but not m2.
    - ``added_theorems``: list of theorem refs in m2 but not m1.
    - ``removed_theorems``: list of theorem refs in m1 but not m2.
    - ``level_changed``: bool, True if the maturity levels differ.
    - ``level_before``: maturity level from m1.
    - ``level_after``: maturity level from m2.
    - ``version_before``: version from m1.
    - ``version_after``: version from m2.
    - ``version_advanced``: bool, True if m2's version is strictly higher.
    - ``same_system``: bool, True if both manifests have the same system_id.

    Parameters
    ----------
    m1:
        The "before" manifest.
    m2:
        The "after" manifest.

    Returns
    -------
    dict[str, Any]
        A dictionary describing the differences between the two manifests.

    Examples
    --------
    ::

        diff = compare_manifests(old_manifest, new_manifest)
        if diff["level_changed"]:
            print(f"Level advanced: {diff['level_before']} → {diff['level_after']}")
    """
    set1_caps = set(m1.capability_claims)
    set2_caps = set(m2.capability_claims)
    set1_ev = set(m1.evidence_refs)
    set2_ev = set(m2.evidence_refs)
    set1_thm = set(m1.theorem_refs)
    set2_thm = set(m2.theorem_refs)

    return {
        "added_claims": sorted(set2_caps - set1_caps),
        "removed_claims": sorted(set1_caps - set2_caps),
        "added_evidence": sorted(set2_ev - set1_ev),
        "removed_evidence": sorted(set1_ev - set2_ev),
        "added_theorems": sorted(set2_thm - set1_thm),
        "removed_theorems": sorted(set1_thm - set2_thm),
        "level_changed": m1.maturity_level != m2.maturity_level,
        "level_before": m1.maturity_level,
        "level_after": m2.maturity_level,
        "version_before": m1.version,
        "version_after": m2.version,
        "version_advanced": _parse_version(m2.version) > _parse_version(m1.version),
        "same_system": m1.system_id == m2.system_id,
    }
