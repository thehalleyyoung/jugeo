"""Core frozen dataclasses for the public_alignment subsystem.

This module defines the four primary data structures used across all stages
of the public-alignment pipeline:

* :class:`PublicClaim`  — a single claim in public documentation, carrying
  both its declared trust level and the internal trust level it was projected
  from.  The key invariant is ``declared_trust_level ≤ internal_trust_level``.

* :class:`HonestProjection`  — the projection functor π_pub applied to a set
  of judgments for a specific audience.  Aggregates PublicClaims and records
  whether the projection is valid.

* :class:`DocumentationSection`  — a versioned unit of public documentation
  derived from internal judgment state.  Carries a trust level and evidence
  channels.

* :class:`MigrationPlan`  — a versioned plan for migrating the public API or
  documentation between two versions, honest about breaking changes and
  preserving semantic content.

Theory basis
------------
From theory2.tex Ch13:

  The projection map π_pub : InternalState → PublicDoc must satisfy

      trust(π_pub(J)) ≤ trust(J)   for all judgments J.

  A violation (``trust(π_pub(J)) > trust(J)``) is a *silent strengthening
  obstruction*, a non-trivial class in Ȟ¹ on the semantic site.  It is
  encoded in ObstructionRecord so it can be tracked, repaired, or surfaced to
  human reviewers.

# copilot: models.py — frozen dataclasses for public_alignment Ch13
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from jugeo.judgments.judgment_terms import TrustLevel
from jugeo.errors import (
    ObstructionRecord,
    FailureScope,
    FailureClassification,
    EvidenceFamily,
    RepairHint,
    RepairPriority,
)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str = "id") -> str:
    """Generate a short unique identifier with the given prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ═══════════════════════════════════════════════════════════════════════════
# §1  PublicClaim
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class PublicClaim:
    """A single claim made in public documentation.

    A PublicClaim records both what trust level the documentation *declares*
    for the claim and what trust level the underlying internal judgment
    *actually* has.  The honesty invariant is:

        declared_trust_level ≤ internal_trust_level

    Any claim that violates this invariant is a *silent strengthening
    obstruction* (Ȟ¹ class).

    Attributes
    ----------
    claim_id : str
        Unique identifier for this claim.
    coordinate : str
        Semantic-site coordinate at which this claim lives.
    statement : str
        Human-readable statement of the claim.
    declared_trust_level : TrustLevel
        The trust level this claim *asserts* in public documentation.
    internal_trust_level : TrustLevel
        The trust level of the underlying internal judgment.
    is_honest : bool | None
        Cached honesty check result; ``None`` if not yet computed.
    publication_timestamp : str
        ISO-8601 timestamp when this claim was published.
    source_judgment_id : str
        Identifier of the Judgment this claim was projected from.
    evidence_summary : str
        Short human-readable summary of the supporting evidence.
    metadata : dict[str, JsonValue]
        Arbitrary additional metadata (audience, version, tags, etc.).
    """

    claim_id: str
    coordinate: str
    statement: str
    declared_trust_level: TrustLevel
    internal_trust_level: TrustLevel
    is_honest: bool | None = None
    publication_timestamp: str = ""
    source_judgment_id: str = ""
    evidence_summary: str = ""
    metadata: dict[str, JsonValue] = field(default_factory=dict)  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Honesty checks
    # ------------------------------------------------------------------

    def check_honesty(self) -> bool:
        """Return ``True`` when the declared trust level ≤ internal trust level.

        This is the core monotonicity check from theory2.tex §13.2.  A claim
        is honest iff it does not over-state its internal evidential basis.

        Returns
        -------
        bool
            ``True`` if honest (declared ≤ internal), ``False`` otherwise.
        """
        return int(self.declared_trust_level) <= int(self.internal_trust_level)

    def honesty_delta(self) -> int:
        """Return ``internal - declared`` trust level integers.

        A non-negative delta means the claim is honest (or conservative).
        A negative delta means the claim is dishonest (silent strengthening).

        Returns
        -------
        int
            ``int(internal_trust_level) - int(declared_trust_level)``.
            Negative values indicate violations.
        """
        return int(self.internal_trust_level) - int(self.declared_trust_level)

    def strengthen_violation(self) -> ObstructionRecord | None:
        """Return an ObstructionRecord if this claim violates the honesty law.

        If the claim is honest (``declared ≤ internal``), returns ``None``.
        Otherwise returns a fully-populated ObstructionRecord describing the
        silent-strengthening obstruction.

        Returns
        -------
        ObstructionRecord | None
            ``None`` if honest; an ``ObstructionRecord`` if violated.
        """
        if self.check_honesty():
            return None
        delta = abs(self.honesty_delta())
        hint = RepairHint(
            action="weaken_public_claim",
            description=(
                f"Lower declared_trust_level from "
                f"{self.declared_trust_level.name} to "
                f"{self.internal_trust_level.name} "
                f"(delta={delta} levels)."
            ),
            priority=RepairPriority.HIGH,
            target_coordinate=self.coordinate,
            estimated_effort="low",
        )
        return ObstructionRecord(
            obstruction_id=_new_id("obs"),
            coordinate=self.coordinate,
            condition_violated="honesty_monotonicity",
            description=(
                f"Silent strengthening detected on claim {self.claim_id!r}: "
                f"declared trust {self.declared_trust_level.name} > "
                f"internal trust {self.internal_trust_level.name}."
            ),
            evidence_family=EvidenceFamily.SEMANTIC,
            trust_at_violation=self.internal_trust_level.value,
            repair_hints=(hint,),
            is_blocking=True,
            source_claim_id=self.claim_id,
        )

    def weaken_to_honest(self) -> "PublicClaim":
        """Return a new PublicClaim with declared trust weakened to be honest.

        If already honest, returns ``self`` unchanged.  Otherwise produces a
        new instance where ``declared_trust_level == internal_trust_level``,
        implementing the canonical repair-by-weakening from theory2.tex §13.2.6.

        Returns
        -------
        PublicClaim
            An honest version of this claim.
        """
        if self.check_honesty():
            return self
        return replace(
            self,
            declared_trust_level=self.internal_trust_level,
            is_honest=True,
            metadata={**self.metadata, "weakened": True, "weakened_at": _now_iso()},
        )

    def with_honesty_checked(self) -> "PublicClaim":
        """Return a copy with ``is_honest`` set to the result of ``check_honesty()``.

        Returns
        -------
        PublicClaim
            Copy with ``is_honest`` populated.
        """
        return replace(self, is_honest=self.check_honesty())

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            Serialized representation.
        """
        return {
            "claim_id": self.claim_id,
            "coordinate": self.coordinate,
            "statement": self.statement,
            "declared_trust_level": self.declared_trust_level.value,
            "internal_trust_level": self.internal_trust_level.value,
            "is_honest": self.is_honest,
            "publication_timestamp": self.publication_timestamp,
            "source_judgment_id": self.source_judgment_id,
            "evidence_summary": self.evidence_summary,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "PublicClaim":
        """Deserialize from a JSON-compatible dictionary.

        Parameters
        ----------
        data : dict[str, JsonValue]
            Dictionary previously produced by ``to_dict()``.

        Returns
        -------
        PublicClaim
            Reconstructed instance.
        """
        return cls(
            claim_id=str(data["claim_id"]),
            coordinate=str(data["coordinate"]),
            statement=str(data["statement"]),
            declared_trust_level=TrustLevel(int(data["declared_trust_level"])),  # type: ignore[arg-type]
            internal_trust_level=TrustLevel(int(data["internal_trust_level"])),  # type: ignore[arg-type]
            is_honest=data.get("is_honest"),  # type: ignore[arg-type]
            publication_timestamp=str(data.get("publication_timestamp", "")),
            source_judgment_id=str(data.get("source_judgment_id", "")),
            evidence_summary=str(data.get("evidence_summary", "")),
            metadata=dict(data.get("metadata") or {}),  # type: ignore[arg-type]
        )

    def __repr__(self) -> str:
        """Short representation for debugging."""
        honest_tag = "honest" if self.check_honesty() else "DISHONEST"
        return (
            f"PublicClaim({self.claim_id!r}, {self.coordinate!r}, "
            f"declared={self.declared_trust_level.name}, "
            f"internal={self.internal_trust_level.name}, {honest_tag})"
        )


# ═══════════════════════════════════════════════════════════════════════════
# §2  HonestProjection
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class HonestProjection:
    """The projection functor π_pub applied to internal judgments.

    A HonestProjection collects the PublicClaims produced by projecting a set
    of internal judgments for a specific audience.  It records a trust ceiling
    (the maximum declared trust level allowed for this audience), the
    projection rules applied, and a cached validity flag.

    Theory basis (theory2.tex §13.3)
    ---------------------------------
    The projection functor must be:

    * **Conservative**: ``trust(π_pub(J)) ≤ trust(J)``
    * **Natural**: commutes with restriction morphisms on the semantic site
    * **Audience-aware**: different audiences may have different trust ceilings

    Attributes
    ----------
    projection_id : str
        Unique identifier for this projection.
    source_coordinate : str
        Coordinate of the root internal judgment.
    target_audience : str
        Identifier of the audience this projection targets.
    projection_rules : tuple[str, ...]
        Names of rules applied during projection (for audit trail).
    trust_ceiling : TrustLevel
        Maximum allowed trust level for any claim in this projection.
    applied_at : str
        ISO-8601 timestamp when the projection was computed.
    claims : tuple[PublicClaim, ...]
        Ordered tuple of all projected public claims.
    is_valid : bool | None
        Cached validity flag; ``None`` if not yet validated.
    """

    projection_id: str
    source_coordinate: str
    target_audience: str
    projection_rules: tuple[str, ...] = ()
    trust_ceiling: TrustLevel = TrustLevel.VERIFIED_PROOF
    applied_at: str = ""
    claims: tuple[PublicClaim, ...] = ()
    is_valid: bool | None = None

    # ------------------------------------------------------------------
    # Projection operations
    # ------------------------------------------------------------------

    def project(self, judgment: Any) -> PublicClaim:
        """Project a single Judgment into a PublicClaim for this audience.

        The projected trust level is the minimum of the judgment's trust
        level and this projection's trust_ceiling.

        Parameters
        ----------
        judgment : Judgment
            Internal judgment to project.

        Returns
        -------
        PublicClaim
            A new PublicClaim with declared trust ≤ internal trust.
        """
        internal_level: TrustLevel = getattr(
            judgment, "trust_level",
            TrustLevel.UNVERIFIED,
        )
        # Apply ceiling — proj(T) ≤ min(T, ceiling)
        if isinstance(internal_level, TrustLevel):
            declared = TrustLevel(min(int(internal_level), int(self.trust_ceiling)))
        else:
            declared = TrustLevel.UNVERIFIED

        coord = getattr(judgment, "coordinate", "")
        if hasattr(coord, "path"):
            coord_str = str(coord.path)
        else:
            coord_str = str(coord)

        prop = getattr(judgment, "proposition", None)
        statement = str(getattr(prop, "content", "")) if prop else str(judgment)
        jid = str(getattr(judgment, "judgment_id", _new_id("jid")))

        claim = PublicClaim(
            claim_id=_new_id("claim"),
            coordinate=coord_str,
            statement=statement,
            declared_trust_level=declared,
            internal_trust_level=internal_level
            if isinstance(internal_level, TrustLevel) else TrustLevel.UNVERIFIED,
            is_honest=True,
            publication_timestamp=_now_iso(),
            source_judgment_id=jid,
            evidence_summary=f"Projected for audience {self.target_audience!r}",
        )
        return claim

    def validate(self) -> bool:
        """Return ``True`` when all claims satisfy the honesty invariant.

        Returns
        -------
        bool
            ``True`` if every claim is honest; ``False`` otherwise.
        """
        return all(c.check_honesty() for c in self.claims)

    def violations(self) -> tuple[PublicClaim, ...]:
        """Return the subset of claims that violate the honesty invariant.

        Returns
        -------
        tuple[PublicClaim, ...]
            Dishonest claims only.
        """
        return tuple(c for c in self.claims if not c.check_honesty())

    def with_claim(self, claim: PublicClaim) -> "HonestProjection":
        """Return a new projection with *claim* appended to ``claims``.

        Parameters
        ----------
        claim : PublicClaim
            The claim to append.

        Returns
        -------
        HonestProjection
            Updated projection.
        """
        return replace(self, claims=(*self.claims, claim), is_valid=None)

    def apply_ceiling(self, level: TrustLevel) -> "HonestProjection":
        """Return a new projection with trust_ceiling set to *level*.

        All existing claims are also weakened to at most *level*.

        Parameters
        ----------
        level : TrustLevel
            The new ceiling to apply.

        Returns
        -------
        HonestProjection
            Updated projection with ceiling enforced.
        """
        new_claims = tuple(
            replace(c, declared_trust_level=TrustLevel(min(int(c.declared_trust_level), int(level))))
            if int(c.declared_trust_level) > int(level) else c
            for c in self.claims
        )
        return replace(self, trust_ceiling=level, claims=new_claims, is_valid=None)

    def claim_count(self) -> int:
        """Return the number of claims in this projection.

        Returns
        -------
        int
            Number of claims.
        """
        return len(self.claims)

    def honest_claims(self) -> tuple[PublicClaim, ...]:
        """Return only the honest claims.

        Returns
        -------
        tuple[PublicClaim, ...]
            Honest claims subset.
        """
        return tuple(c for c in self.claims if c.check_honesty())

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            Serialized representation.
        """
        return {
            "projection_id": self.projection_id,
            "source_coordinate": self.source_coordinate,
            "target_audience": self.target_audience,
            "projection_rules": list(self.projection_rules),
            "trust_ceiling": self.trust_ceiling.value,
            "applied_at": self.applied_at,
            "claims": [c.to_dict() for c in self.claims],
            "is_valid": self.is_valid,
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "HonestProjection":
        """Deserialize from a JSON-compatible dictionary.

        Parameters
        ----------
        data : dict[str, JsonValue]
            Dictionary previously produced by ``to_dict()``.

        Returns
        -------
        HonestProjection
            Reconstructed instance.
        """
        return cls(
            projection_id=str(data["projection_id"]),
            source_coordinate=str(data["source_coordinate"]),
            target_audience=str(data["target_audience"]),
            projection_rules=tuple(str(r) for r in (data.get("projection_rules") or [])),
            trust_ceiling=TrustLevel(int(data.get("trust_ceiling", TrustLevel.VERIFIED_PROOF.value))),  # type: ignore[arg-type]
            applied_at=str(data.get("applied_at", "")),
            claims=tuple(PublicClaim.from_dict(c) for c in (data.get("claims") or [])),  # type: ignore[arg-type]
            is_valid=data.get("is_valid"),  # type: ignore[arg-type]
        )

    def __repr__(self) -> str:
        """Short representation for debugging."""
        return (
            f"HonestProjection({self.projection_id!r}, "
            f"audience={self.target_audience!r}, "
            f"claims={len(self.claims)}, "
            f"ceiling={self.trust_ceiling.name}, "
            f"valid={self.is_valid})"
        )


# ═══════════════════════════════════════════════════════════════════════════
# §3  DocumentationSection
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class DocumentationSection:
    """A versioned section of public documentation.

    A DocumentationSection represents a unit of public-facing documentation
    derived from internal judgment state.  It carries a trust level, a set of
    evidence channels, and versioning information.

    Theory basis (theory2.tex §13.3.2)
    ------------------------------------
    Sections are the image of the projection functor on a coordinate
    neighborhood.  Each section carries an explicit trust level that must be
    conservative with respect to the internal judgments that produced it.

    Attributes
    ----------
    section_id : str
        Unique identifier.
    title : str
        Section heading.
    content : str
        Markdown or plain-text content of the section.
    coordinate : str
        Semantic-site coordinate this section covers.
    version : str
        Version string (e.g., ``"1.0.0"``).
    trust_level : TrustLevel
        Trust level of the content in this section.
    evidence_channels : tuple[str, ...]
        Names of evidence channels that contributed to this section.
    last_updated : str
        ISO-8601 timestamp of the last update.
    parent_section_id : str
        ID of the parent section, or empty if top-level.
    subsections : tuple[str, ...]
        IDs of immediate child sections.
    is_public : bool
        Whether this section is currently visible to the public.
    metadata : dict[str, JsonValue]
        Arbitrary additional metadata.
    """

    section_id: str
    title: str
    content: str
    coordinate: str
    version: str = "1.0.0"
    trust_level: TrustLevel = TrustLevel.UNVERIFIED
    evidence_channels: tuple[str, ...] = ()
    last_updated: str = ""
    parent_section_id: str = ""
    subsections: tuple[str, ...] = ()
    is_public: bool = True
    metadata: dict[str, JsonValue] = field(default_factory=dict)  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Content operations
    # ------------------------------------------------------------------

    def update_content(self, new_content: str) -> "DocumentationSection":
        """Return a copy with updated content and a refreshed timestamp.

        Parameters
        ----------
        new_content : str
            New content for the section.

        Returns
        -------
        DocumentationSection
            Updated section.
        """
        return replace(self, content=new_content, last_updated=_now_iso())

    def project_to_public(self) -> "DocumentationSection":
        """Return a public-safe copy with ``is_public=True``.

        Ensures the section is marked public and strips any metadata keys
        that are marked as internal-only (prefixed with ``"_internal"``).

        Returns
        -------
        DocumentationSection
            Public version of this section.
        """
        clean_meta = {k: v for k, v in self.metadata.items() if not k.startswith("_internal")}
        return replace(self, is_public=True, metadata=clean_meta, last_updated=_now_iso())

    def deprecate(self, reason: str) -> "DocumentationSection":
        """Return a copy marked as deprecated with the given reason.

        Parameters
        ----------
        reason : str
            Explanation of why this section is deprecated.

        Returns
        -------
        DocumentationSection
            Deprecated section.
        """
        new_meta = {**self.metadata, "deprecated": True, "deprecation_reason": reason, "deprecated_at": _now_iso()}
        return replace(
            self,
            is_public=False,
            metadata=new_meta,
            last_updated=_now_iso(),
        )

    def is_stale(self) -> bool:
        """Return ``True`` if this section has not been updated in over 90 days.

        Uses the ``last_updated`` timestamp; returns ``False`` if it is absent.

        Returns
        -------
        bool
            ``True`` if stale.
        """
        if not self.last_updated:
            return False
        import datetime
        try:
            updated = datetime.datetime.strptime(self.last_updated, "%Y-%m-%dT%H:%M:%SZ")
            now = datetime.datetime.utcnow()
            return (now - updated).days > 90
        except ValueError:
            return False

    def summary(self) -> str:
        """Return a one-line summary of this section.

        Returns
        -------
        str
            Summary string.
        """
        pub = "public" if self.is_public else "private"
        return (
            f"Section({self.section_id!r}, {self.title!r}, "
            f"v={self.version}, trust={self.trust_level.name}, {pub})"
        )

    def with_trust_level(self, level: TrustLevel) -> "DocumentationSection":
        """Return a copy with the given trust level.

        Parameters
        ----------
        level : TrustLevel
            New trust level.

        Returns
        -------
        DocumentationSection
            Updated section.
        """
        return replace(self, trust_level=level, last_updated=_now_iso())

    def add_subsection(self, subsection_id: str) -> "DocumentationSection":
        """Return a copy with *subsection_id* added to ``subsections``.

        Parameters
        ----------
        subsection_id : str
            ID of the subsection to add.

        Returns
        -------
        DocumentationSection
            Updated section.
        """
        if subsection_id in self.subsections:
            return self
        return replace(self, subsections=(*self.subsections, subsection_id))

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            Serialized representation.
        """
        return {
            "section_id": self.section_id,
            "title": self.title,
            "content": self.content,
            "coordinate": self.coordinate,
            "version": self.version,
            "trust_level": self.trust_level.value,
            "evidence_channels": list(self.evidence_channels),
            "last_updated": self.last_updated,
            "parent_section_id": self.parent_section_id,
            "subsections": list(self.subsections),
            "is_public": self.is_public,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "DocumentationSection":
        """Deserialize from a JSON-compatible dictionary.

        Parameters
        ----------
        data : dict[str, JsonValue]
            Dictionary previously produced by ``to_dict()``.

        Returns
        -------
        DocumentationSection
            Reconstructed instance.
        """
        return cls(
            section_id=str(data["section_id"]),
            title=str(data["title"]),
            content=str(data["content"]),
            coordinate=str(data["coordinate"]),
            version=str(data.get("version", "1.0.0")),
            trust_level=TrustLevel(int(data.get("trust_level", TrustLevel.UNVERIFIED.value))),  # type: ignore[arg-type]
            evidence_channels=tuple(str(c) for c in (data.get("evidence_channels") or [])),
            last_updated=str(data.get("last_updated", "")),
            parent_section_id=str(data.get("parent_section_id", "")),
            subsections=tuple(str(s) for s in (data.get("subsections") or [])),
            is_public=bool(data.get("is_public", True)),
            metadata=dict(data.get("metadata") or {}),  # type: ignore[arg-type]
        )

    def __repr__(self) -> str:
        """Short representation for debugging."""
        return self.summary()


# ═══════════════════════════════════════════════════════════════════════════
# §4  MigrationPlan  (with inner MigrationStep)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """A versioned plan for migrating public API or documentation.

    A MigrationPlan records what changes are required to migrate from one
    version of a public-facing API or documentation set to another.  It is
    honest about breaking changes, declares what semantic content is preserved,
    and tracks the trust impact of each migration step.

    Theory basis (theory2.tex §13.5)
    ----------------------------------
    A migration plan must satisfy:

    (a) Semantic preservation: the plan must list which old claims remain
        valid after migration (``preserved_semantics``).
    (b) Honesty about breaks: all breaking changes must be listed in
        ``breaking_changes``; none may be hidden.
    (c) Trust monotonicity: if a claim's trust level changes, it may only
        decrease (or stay the same) unless new evidence is explicitly
        provided.

    Attributes
    ----------
    plan_id : str
        Unique identifier.
    source_version : str
        Version being migrated from.
    target_version : str
        Version being migrated to.
    coordinate : str
        Coordinate of the subsystem being migrated.
    steps : tuple[MigrationStep, ...]
        Ordered migration steps.
    breaking_changes : tuple[str, ...]
        Human-readable descriptions of breaking changes.
    preserved_semantics : tuple[str, ...]
        Claims that remain valid after migration.
    deprecated_claims : tuple[str, ...]
        Claims that are deprecated by this migration.
    new_claims : tuple[str, ...]
        New claims introduced by this migration.
    confidence : float
        Confidence score in [0.0, 1.0] that the plan is complete.
    created_at : str
        ISO-8601 creation timestamp.
    """

    @dataclass(frozen=True, slots=True)
    class MigrationStep:
        """A single step in a migration plan.

        Attributes
        ----------
        step_id : str
            Unique identifier for this step.
        description : str
            Human-readable description of what this step does.
        is_breaking : bool
            Whether this step introduces a breaking change.
        old_claim : str
            The claim (or API surface) being changed.
        new_claim : str
            The replacement claim (or API surface).
        migration_note : str
            Guidance for consumers of the public API.
        trust_impact : int
            Change in trust level (negative = weaker, 0 = no change,
            positive = stronger only if new evidence supplied).
        """

        step_id: str
        description: str
        is_breaking: bool = False
        old_claim: str = ""
        new_claim: str = ""
        migration_note: str = ""
        trust_impact: int = 0

        def to_dict(self) -> dict[str, JsonValue]:
            """Serialize to dict.

            Returns
            -------
            dict[str, JsonValue]
                Serialized step.
            """
            return {
                "step_id": self.step_id,
                "description": self.description,
                "is_breaking": self.is_breaking,
                "old_claim": self.old_claim,
                "new_claim": self.new_claim,
                "migration_note": self.migration_note,
                "trust_impact": self.trust_impact,
            }

        @classmethod
        def from_dict(cls, data: dict[str, JsonValue]) -> "MigrationPlan.MigrationStep":
            """Deserialize from dict.

            Parameters
            ----------
            data : dict[str, JsonValue]
                Dictionary previously produced by ``to_dict()``.

            Returns
            -------
            MigrationPlan.MigrationStep
                Reconstructed step.
            """
            return cls(
                step_id=str(data["step_id"]),
                description=str(data["description"]),
                is_breaking=bool(data.get("is_breaking", False)),
                old_claim=str(data.get("old_claim", "")),
                new_claim=str(data.get("new_claim", "")),
                migration_note=str(data.get("migration_note", "")),
                trust_impact=int(data.get("trust_impact", 0)),  # type: ignore[arg-type]
            )

    # MigrationPlan fields
    plan_id: str
    source_version: str
    target_version: str
    coordinate: str
    steps: tuple[MigrationStep, ...] = ()
    breaking_changes: tuple[str, ...] = ()
    preserved_semantics: tuple[str, ...] = ()
    deprecated_claims: tuple[str, ...] = ()
    new_claims: tuple[str, ...] = ()
    confidence: float = 1.0
    created_at: str = ""

    # ------------------------------------------------------------------
    # Analysis methods
    # ------------------------------------------------------------------

    def is_honest(self) -> bool:
        """Return ``True`` when no step silently increases trust.

        A migration plan is honest when every step with ``trust_impact > 0``
        would need explicit new evidence (this method checks the flag
        structurally; full evidence-backed validation is in ``validate_honesty``).

        Returns
        -------
        bool
            ``True`` if no step silently strengthens trust.
        """
        for step in self.steps:
            if step.trust_impact > 0 and not step.migration_note:
                return False
        return True

    def breaking_step_count(self) -> int:
        """Return the count of steps marked ``is_breaking=True``.

        Returns
        -------
        int
            Number of breaking steps.
        """
        return sum(1 for s in self.steps if s.is_breaking)

    def semantic_coverage(self) -> float:
        """Return the fraction of old claims covered by preserved or new claims.

        Estimated as:
            (len(preserved_semantics) + len(new_claims)) /
            max(1, len(deprecated_claims) + len(preserved_semantics))

        Returns
        -------
        float
            Coverage in [0.0, 1.0].
        """
        total_old = len(self.deprecated_claims) + len(self.preserved_semantics)
        if total_old == 0:
            return 1.0
        covered = len(self.preserved_semantics) + len(self.new_claims)
        return min(1.0, covered / total_old)

    def with_step(self, step: "MigrationPlan.MigrationStep") -> "MigrationPlan":
        """Return a copy with *step* appended.

        Parameters
        ----------
        step : MigrationPlan.MigrationStep
            Step to append.

        Returns
        -------
        MigrationPlan
            Updated plan.
        """
        new_breaking = self.breaking_changes
        if step.is_breaking and step.description not in new_breaking:
            new_breaking = (*new_breaking, step.description)
        return replace(self, steps=(*self.steps, step), breaking_changes=new_breaking)

    def validate_honesty(self) -> tuple[ObstructionRecord, ...]:
        """Return ObstructionRecords for any steps that silently strengthen trust.

        A step with ``trust_impact > 0`` and no ``migration_note`` is treated
        as a potential silent strengthening, generating an ObstructionRecord.

        Returns
        -------
        tuple[ObstructionRecord, ...]
            Zero or more obstruction records.
        """
        violations: list[ObstructionRecord] = []
        for step in self.steps:
            if step.trust_impact > 0 and not step.migration_note:
                hint = RepairHint(
                    action="add_migration_evidence",
                    description=(
                        f"Step {step.step_id!r} increases trust by {step.trust_impact} "
                        f"without a migration_note; provide explicit evidence."
                    ),
                    priority=RepairPriority.HIGH,
                    target_coordinate=self.coordinate,
                    estimated_effort="medium",
                )
                violations.append(
                    ObstructionRecord(
                        obstruction_id=_new_id("obs"),
                        coordinate=self.coordinate,
                        condition_violated="migration_trust_monotonicity",
                        description=(
                            f"Migration step {step.step_id!r} silently strengthens trust "
                            f"(impact=+{step.trust_impact}) without justification."
                        ),
                        evidence_family=EvidenceFamily.SEMANTIC,
                        trust_at_violation=0,
                        repair_hints=(hint,),
                        is_blocking=True,
                        source_claim_id=step.step_id,
                    )
                )
        return tuple(violations)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            Serialized representation.
        """
        return {
            "plan_id": self.plan_id,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "coordinate": self.coordinate,
            "steps": [s.to_dict() for s in self.steps],
            "breaking_changes": list(self.breaking_changes),
            "preserved_semantics": list(self.preserved_semantics),
            "deprecated_claims": list(self.deprecated_claims),
            "new_claims": list(self.new_claims),
            "confidence": self.confidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "MigrationPlan":
        """Deserialize from a JSON-compatible dictionary.

        Parameters
        ----------
        data : dict[str, JsonValue]
            Dictionary previously produced by ``to_dict()``.

        Returns
        -------
        MigrationPlan
            Reconstructed instance.
        """
        steps = tuple(
            MigrationPlan.MigrationStep.from_dict(s)  # type: ignore[arg-type]
            for s in (data.get("steps") or [])
        )
        return cls(
            plan_id=str(data["plan_id"]),
            source_version=str(data["source_version"]),
            target_version=str(data["target_version"]),
            coordinate=str(data["coordinate"]),
            steps=steps,
            breaking_changes=tuple(str(c) for c in (data.get("breaking_changes") or [])),
            preserved_semantics=tuple(str(s) for s in (data.get("preserved_semantics") or [])),
            deprecated_claims=tuple(str(c) for c in (data.get("deprecated_claims") or [])),
            new_claims=tuple(str(c) for c in (data.get("new_claims") or [])),
            confidence=float(data.get("confidence", 1.0)),  # type: ignore[arg-type]
            created_at=str(data.get("created_at", "")),
        )

    def __repr__(self) -> str:
        """Short representation for debugging."""
        return (
            f"MigrationPlan({self.plan_id!r}, "
            f"{self.source_version!r}→{self.target_version!r}, "
            f"steps={len(self.steps)}, "
            f"breaking={self.breaking_step_count()}, "
            f"confidence={self.confidence:.2f})"
        )


# ---------------------------------------------------------------------------
# §5  Convenience factory functions
# ---------------------------------------------------------------------------

def make_public_claim(
    coordinate: str,
    statement: str,
    declared: TrustLevel,
    internal: TrustLevel,
    source_judgment_id: str = "",
) -> PublicClaim:
    """Convenience factory for creating a PublicClaim.

    Parameters
    ----------
    coordinate : str
        Semantic site coordinate.
    statement : str
        Human-readable claim text.
    declared : TrustLevel
        Trust level declared in public documentation.
    internal : TrustLevel
        Actual internal trust level.
    source_judgment_id : str
        ID of the source judgment.

    Returns
    -------
    PublicClaim
        Newly created claim with honesty pre-checked.
    """
    claim = PublicClaim(
        claim_id=_new_id("claim"),
        coordinate=coordinate,
        statement=statement,
        declared_trust_level=declared,
        internal_trust_level=internal,
        publication_timestamp=_now_iso(),
        source_judgment_id=source_judgment_id,
    )
    return claim.with_honesty_checked()


def make_honest_projection(
    source_coordinate: str,
    target_audience: str,
    trust_ceiling: TrustLevel = TrustLevel.VERIFIED_PROOF,
) -> HonestProjection:
    """Convenience factory for creating an empty HonestProjection.

    Parameters
    ----------
    source_coordinate : str
        Coordinate of the root internal judgment.
    target_audience : str
        Identifier of the audience.
    trust_ceiling : TrustLevel
        Maximum allowed declared trust level.

    Returns
    -------
    HonestProjection
        New empty projection.
    """
    return HonestProjection(
        projection_id=_new_id("proj"),
        source_coordinate=source_coordinate,
        target_audience=target_audience,
        trust_ceiling=trust_ceiling,
        applied_at=_now_iso(),
    )


def make_documentation_section(
    title: str,
    content: str,
    coordinate: str,
    trust_level: TrustLevel = TrustLevel.UNVERIFIED,
    version: str = "1.0.0",
) -> DocumentationSection:
    """Convenience factory for DocumentationSection.

    Parameters
    ----------
    title : str
        Section heading.
    content : str
        Section content.
    coordinate : str
        Semantic site coordinate.
    trust_level : TrustLevel
        Trust level of the content.
    version : str
        Version string.

    Returns
    -------
    DocumentationSection
        New section.
    """
    return DocumentationSection(
        section_id=_new_id("sec"),
        title=title,
        content=content,
        coordinate=coordinate,
        trust_level=trust_level,
        version=version,
        last_updated=_now_iso(),
    )


def make_migration_plan(
    source_version: str,
    target_version: str,
    coordinate: str,
) -> MigrationPlan:
    """Convenience factory for an empty MigrationPlan.

    Parameters
    ----------
    source_version : str
        Version being migrated from.
    target_version : str
        Version being migrated to.
    coordinate : str
        Semantic site coordinate.

    Returns
    -------
    MigrationPlan
        New empty migration plan.
    """
    return MigrationPlan(
        plan_id=_new_id("plan"),
        source_version=source_version,
        target_version=target_version,
        coordinate=coordinate,
        created_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# §6  Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "PublicClaim",
    "HonestProjection",
    "DocumentationSection",
    "MigrationPlan",
    # Factories
    "make_public_claim",
    "make_honest_projection",
    "make_documentation_section",
    "make_migration_plan",
    # Helpers
    "JsonScalar",
    "JsonValue",
]

# copilot: models.py — Ch13 public_alignment frozen dataclasses
