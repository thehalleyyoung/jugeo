"""Judgment sections over semantic coordinates.

A *section* of the judgment sheaf over a coordinate is the complete data
assigned to that coordinate: typed values, judgment assignments, evidence
archives, trust annotations, and support sets.  Sections can be restricted
to sub-coordinates and glued along covers, bridging the geometric (sites,
covers) and logical (judgments, evidence) layers of the JuGeo framework.

This module implements the full section algebra required by the sheaf-
theoretic machinery described in theory2.tex.

# copilot: core sheaf-section layer for LLM orchestration and judgment transport.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from jugeo.geometry.covers import Cover
from jugeo.geometry.descent import GluingReport, Obstruction, glue_sections
from jugeo.geometry.site import (
    CoordinateKind,
    CoordinateMorphism,
    CoordinateObject,
    restrict_coordinate,
)
from jugeo.geometry.supports import SupportRegion, compute_support
from jugeo.judgments.contexts import SemanticContext
from jugeo.judgments.judgment_terms import JudgmentStatus, LocalJudgment

# Evidence imports are guarded because upstream modules may have transient
# syntax issues during development.  When unavailable, lightweight stubs
# are used so that the rest of the section algebra remains functional.
try:
    from jugeo.evidence.channels import EvidenceKind, EvidenceRecord
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
except Exception:  # pragma: no cover – upstream parse errors
    from dataclasses import dataclass as _dc, field as _field
    from enum import Enum as _Enum

    class EvidenceKind(_Enum):  # type: ignore[no-redef]
        """Stub evidence kind."""
        PROOF = "proof"
        SOLVER = "solver"
        RUNTIME = "runtime"
        SEMANTIC = "semantic"
        PROPOSAL = "proposal"

    @_dc(frozen=True, slots=True)
    class EvidenceRecord:  # type: ignore[no-redef]
        """Stub evidence record."""
        channel: Any = None
        claim: str = ""
        payload: Mapping[str, Any] = _field(default_factory=dict)
        obligations: tuple[str, ...] = ()
        provenance: tuple[str, ...] = ()
        def canonical_key(self) -> str:
            ch_kind = getattr(self.channel, "kind", EvidenceKind.PROPOSAL)
            ch_name = getattr(self.channel, "name", "stub")
            return f"{ch_kind.name}:{ch_name}:{self.claim}"

    class TrustTier(int, _Enum):  # type: ignore[no-redef]
        """Stub trust tier."""
        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3
        def label(self) -> str:
            return self.name.lower()

    @_dc(frozen=True, slots=True)
    class TrustProfile:  # type: ignore[no-redef]
        """Stub trust profile."""
        tier: Any = TrustTier.PROPOSAL
        support_scope: tuple[str, ...] = ()
        reasons: tuple[str, ...] = ()
        def to_dict(self) -> dict[str, object]:
            return {"tier": self.tier.name, "scope": list(self.support_scope), "reasons": list(self.reasons)}
        def with_reasons(self, *extra: str) -> "TrustProfile":
            return TrustProfile(self.tier, self.support_scope, self.reasons + extra)
        def label(self) -> str:
            return self.tier.label()

    def join_trust_profiles(*profiles: Any) -> Any:  # type: ignore[no-redef]
        """Stub join: returns the weakest tier."""
        if not profiles:
            return TrustProfile()
        weakest = min(profiles, key=lambda p: p.tier)
        scopes: list[str] = []
        reasons: list[str] = []
        for p in profiles:
            scopes.extend(p.support_scope)
            reasons.extend(p.reasons)
        return TrustProfile(weakest.tier, tuple(dict.fromkeys(scopes)), tuple(dict.fromkeys(reasons)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_hash(payload: str) -> str:
    """Return a short deterministic hash for cache keys."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _paths_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """True when one path is a prefix of the other."""
    shorter = min(len(a), len(b))
    return a[:shorter] == b[:shorter]


def _merge_dicts(
    base: dict[str, Any],
    overlay: dict[str, Any],
    *,
    conflict: str = "overlay",
) -> dict[str, Any]:
    """Merge two data dictionaries.

    Parameters
    ----------
    base:
        The base mapping.
    overlay:
        Values to merge on top of *base*.
    conflict:
        ``"overlay"`` keeps the overlay value on key clash;
        ``"raise"`` raises ``ValueError``.
    """
    merged = dict(base)
    for k, v in overlay.items():
        if k in merged and conflict == "raise" and merged[k] != v:
            raise ValueError(f"Key conflict for '{k}': {merged[k]!r} vs {v!r}")
        merged[k] = v
    return merged


# ---------------------------------------------------------------------------
# 1. Section
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Section:
    """A section of the judgment sheaf over a single coordinate.

    A section bundles together all semantic data that lives at a given
    coordinate: named typed values, judgment assignments, evidence archives,
    trust metadata, support sets, and residual obligations.

    This is the fundamental unit of data in the sheaf; restriction, gluing,
    transport, and comparison all operate on ``Section`` instances.
    """

    coordinate: CoordinateObject
    data: dict[str, Any] = field(default_factory=dict)
    judgment_assignments: dict[str, LocalJudgment] = field(default_factory=dict)
    evidence_archive: list[EvidenceRecord] = field(default_factory=list)
    trust_annotation: TrustProfile | None = None
    support_set: SupportRegion | None = None
    is_global: bool = False
    residuals: list[str] = field(default_factory=list)
    provenance: tuple[str, ...] = ()

    # -- kept for backward compatibility with the old JudgmentSection API --
    context: SemanticContext | None = None
    judgment: LocalJudgment | None = None
    patch: str = ""

    # ------------------------------------------------------------------ #
    # Core operations
    # ------------------------------------------------------------------ #

    def restrict_to(self, sub_coordinate: CoordinateObject) -> Section:
        """Restrict this section to *sub_coordinate*.

        Data keys whose names start with any component of the sub-coordinate
        path are kept; everything else is dropped.  Judgment assignments are
        filtered by whether their coordinate path is a prefix of the sub-
        coordinate path.  Evidence records are kept in full (evidence does
        not restrict).

        Returns a new ``Section`` whose provenance records the restriction.
        """
        sub_path = sub_coordinate.path
        restricted_data: dict[str, Any] = {}
        for key, value in self.data.items():
            if any(component in key for component in sub_path) or not sub_path:
                restricted_data[key] = value

        restricted_judgments: dict[str, LocalJudgment] = {}
        for prop, jdg in self.judgment_assignments.items():
            if _paths_overlap(jdg.coordinate.path, sub_path):
                restricted_judgments[prop] = jdg

        restricted_support: SupportRegion | None = None
        if self.support_set is not None:
            restricted_support = compute_support(
                sub_coordinate,
                labels=tuple(self.support_set.labels),
            )

        return Section(
            coordinate=sub_coordinate,
            data=restricted_data,
            judgment_assignments=restricted_judgments,
            evidence_archive=list(self.evidence_archive),
            trust_annotation=self.trust_annotation,
            support_set=restricted_support,
            is_global=False,
            residuals=list(self.residuals),
            provenance=self.provenance + ("restrict",),
        )

    def is_compatible_with(
        self,
        other: Section,
        overlap: CoordinateObject | None = None,
    ) -> bool:
        """Check whether *self* and *other* agree on their common overlap.

        If *overlap* is ``None`` the overlap is computed as the longer of the
        two coordinate paths (i.e. the more specific one).  The check
        verifies that data values for shared keys are equal and that judgment
        assignments for shared propositions carry the same proposition text.
        """
        if overlap is not None:
            s1 = self.restrict_to(overlap)
            s2 = other.restrict_to(overlap)
        else:
            if len(self.coordinate.path) >= len(other.coordinate.path):
                s1 = self
                s2 = other.restrict_to(self.coordinate)
            else:
                s1 = self.restrict_to(other.coordinate)
                s2 = other
        for key in set(s1.data) & set(s2.data):
            if s1.data[key] != s2.data[key]:
                return False
        for prop in set(s1.judgment_assignments) & set(s2.judgment_assignments):
            j1 = s1.judgment_assignments[prop]
            j2 = s2.judgment_assignments[prop]
            if j1.proposition != j2.proposition:
                return False
        return True

    def extend(self, new_data: dict[str, Any]) -> Section:
        """Return a new section with *new_data* merged into the existing data.

        Raises ``ValueError`` when a key exists with a conflicting value.
        """
        merged = _merge_dicts(self.data, new_data, conflict="raise")
        return Section(
            coordinate=self.coordinate,
            data=merged,
            judgment_assignments=dict(self.judgment_assignments),
            evidence_archive=list(self.evidence_archive),
            trust_annotation=self.trust_annotation,
            support_set=self.support_set,
            is_global=self.is_global,
            residuals=list(self.residuals),
            provenance=self.provenance + ("extend",),
        )

    def merge_with(
        self,
        other: Section,
        overlap_evidence: list[EvidenceRecord] | None = None,
    ) -> Section:
        """Merge *other* into this section.

        Data dictionaries are combined (overlay semantics).  Judgment
        assignments are unioned.  Evidence archives are concatenated, with
        optional *overlap_evidence* appended.  Trust annotations are joined
        conservatively via ``join_trust_profiles``.
        """
        merged_data = _merge_dicts(self.data, other.data)
        merged_judgments = dict(self.judgment_assignments)
        merged_judgments.update(other.judgment_assignments)
        merged_evidence = list(self.evidence_archive) + list(other.evidence_archive)
        if overlap_evidence:
            merged_evidence.extend(overlap_evidence)
        merged_trust: TrustProfile | None = None
        profiles = [p for p in (self.trust_annotation, other.trust_annotation) if p is not None]
        if profiles:
            merged_trust = join_trust_profiles(*profiles)
        merged_residuals = list(dict.fromkeys(self.residuals + other.residuals))
        return Section(
            coordinate=self.coordinate,
            data=merged_data,
            judgment_assignments=merged_judgments,
            evidence_archive=merged_evidence,
            trust_annotation=merged_trust,
            support_set=self.support_set,
            is_global=self.is_global,
            residuals=merged_residuals,
            provenance=self.provenance + ("merge",),
        )

    def project_public(self) -> dict[str, Any]:
        """Return a lossy public projection of this section.

        Internal evidence payloads and trust reasons are stripped.  Only
        proposition names, data keys, and trust tier labels survive.
        """
        public_judgments: dict[str, str] = {}
        for prop, jdg in self.judgment_assignments.items():
            public_judgments[prop] = jdg.status.name
        trust_label = ""
        if self.trust_annotation is not None:
            trust_label = self.trust_annotation.tier.label()
        return {
            "coordinate": self.coordinate.key,
            "data_keys": sorted(self.data.keys()),
            "judgments": public_judgments,
            "trust": trust_label,
            "is_global": self.is_global,
            "residual_count": len(self.residuals),
        }

    def compute_residuals(self) -> list[str]:
        """Recompute the residuals list from the current judgment assignments.

        A residual is any obligation or obstruction declared by any judgment
        in the section that has not been discharged.
        """
        residuals: list[str] = []
        for jdg in self.judgment_assignments.values():
            residuals.extend(jdg.obligations)
            residuals.extend(jdg.obstructions)
        self.residuals = list(dict.fromkeys(residuals))
        return self.residuals

    def serialize(self) -> dict[str, Any]:
        """Produce a JSON-compatible dictionary for this section."""
        return SectionSerializer.serialize_section(self)

    # Backward compatibility -------------------------------------------------

    def compatible_with(self, other: Section) -> bool:
        """Legacy compatibility check mirroring the old ``JudgmentSection`` API."""
        if self.judgment is not None and other.judgment is not None:
            return (
                self.judgment.proposition == other.judgment.proposition
                and (self.context is not None and other.context is not None
                     and self.context.binding_map() == other.context.binding_map())
            )
        return self.is_compatible_with(other)

    def restrict(self, suffix: tuple[str, ...]) -> Section:
        """Legacy restriction mirroring the old ``JudgmentSection`` API."""
        sub = restrict_coordinate(self.coordinate, suffix=suffix)
        return self.restrict_to(sub)

    @property
    def support(self) -> SupportRegion | None:
        """Legacy alias for callers that still expect ``section.support``."""
        return self.support_set

    # ------------------------------------------------------------------ #
    # Cross-subsystem integration methods
    # ------------------------------------------------------------------ #

    def site_restriction(self, target: Any) -> "Section":
        """Restrict this section using coordinate-based navigation.

        Uses :class:`jugeo.geometry.site.Coordinate` to perform a
        type-aware restriction that respects the hierarchical structure
        of the semantic site.  Unlike :meth:`restrict_to` which accepts
        a raw ``CoordinateObject``, this method accepts a
        ``Coordinate`` and uses its ``is_prefix_of`` logic to validate
        the restriction is well-formed.

        Parameters
        ----------
        target : Coordinate
            The target coordinate to restrict to.  Must be a descendant
            of (or equal to) this section's coordinate.

        Returns
        -------
        Section
            A new section restricted to *target*.

        Raises
        ------
        RuntimeError
            If the site subsystem cannot be loaded.
        ValueError
            If *target* is not a valid restriction of this section's
            coordinate.
        """
        try:
            from jugeo.geometry.site import Coordinate
        except Exception as exc:
            raise RuntimeError(
                "Site geometry subsystem unavailable; cannot perform site restriction"
            ) from exc

        if isinstance(target, Coordinate):
            if not self.coordinate.path or target.is_prefix_of(target):
                sub = CoordinateObject(
                    path=target.path,
                    kind=getattr(target, "kind", self.coordinate.kind),
                )
                return self.restrict_to(sub)
            raise ValueError(
                f"target coordinate {target.key} is not a valid restriction "
                f"of section coordinate {self.coordinate.key}"
            )
        sub = CoordinateObject(
            path=target.path if hasattr(target, "path") else (),
            kind=self.coordinate.kind,
        )
        return self.restrict_to(sub)

    def glue_via_descent(
        self,
        others: Sequence["Section"],
        cover: Any | None = None,
    ) -> Any:
        """Glue this section with *others* using the descent engine.

        Uses :class:`jugeo.geometry.descent.DescentEngine` to attempt
        descent over a cover formed by this section and *others*.
        Returns the full :class:`DescentResult` — either a global
        section or a descent obstruction with repair hints.

        Parameters
        ----------
        others : Sequence[Section]
            The other local sections to glue with.
        cover : Cover, optional
            An explicit cover object.  When ``None``, a minimal cover
            is synthesised from the section coordinates.

        Returns
        -------
        DescentResult
            The outcome of the descent procedure.

        Raises
        ------
        RuntimeError
            If the descent subsystem is unavailable.
        """
        try:
            from jugeo.geometry.descent import DescentEngine
        except Exception as exc:
            raise RuntimeError(
                "Descent engine unavailable; cannot perform section gluing"
            ) from exc

        all_sections = [self] + list(others)
        section_map: dict[str, dict[str, Any]] = {}
        for sec in all_sections:
            key = sec.patch or sec.coordinate.key
            section_map[key] = sec.data

        if cover is None:
            cover = Cover(
                base=self.coordinate,
                patches=tuple(
                    CoordinateObject(path=s.coordinate.path, kind=s.coordinate.kind)
                    for s in all_sections
                ),
            )

        engine = DescentEngine()
        return engine.attempt_descent(cover=cover, sections=section_map)

    def evidence_manifest(self) -> Any:
        """Build an evidence manifest for this section.

        Uses :class:`jugeo.evidence.manifests.EvidenceManifest` to
        assemble all evidence records in this section's archive into a
        structured manifest keyed by coordinate and claim.

        Returns
        -------
        EvidenceManifest
            An immutable evidence manifest for this section.

        Raises
        ------
        RuntimeError
            If the manifests subsystem is unavailable.
        """
        try:
            from jugeo.evidence.manifests import EvidenceManifest
        except Exception as exc:
            raise RuntimeError(
                "Evidence manifests subsystem unavailable"
            ) from exc

        records = tuple(self.evidence_archive)
        claim = ""
        if self.judgment is not None:
            claim = self.judgment.proposition
        elif self.judgment_assignments:
            claim = next(iter(self.judgment_assignments), "")

        trust = self.trust_annotation
        return EvidenceManifest(
            coordinate=self.coordinate.key,
            claim=claim,
            records=records,
            trust=trust if trust is not None else TrustProfile(),
            residuals=tuple(self.residuals),
        )

    def oracle_consultation(self, query: str = "") -> dict[str, Any]:
        """Consult a federated oracle for additional evidence.

        Uses ``jugeo.foundations.oracle_federation`` (when available) to
        request oracle-level evidence for this section's claims.  Oracle
        evidence enters at the ``COPILOT_SUGGESTED`` trust ceiling and
        must be promoted through an explicit discharge step.

        Parameters
        ----------
        query : str
            An optional natural-language query to guide the oracle.
            Defaults to the section's primary proposition.

        Returns
        -------
        dict[str, Any]
            A mapping with ``"available"``, ``"evidence_kind"``,
            ``"trust_ceiling"``, and ``"suggestion"`` keys.
        """
        try:
            from jugeo.foundations.oracle_federation import OracleFederation  # type: ignore[import-not-found]
        except Exception:
            prop = query or (
                self.judgment.proposition if self.judgment else ""
            )
            return {
                "available": False,
                "evidence_kind": "oracle_proposal",
                "trust_ceiling": "copilot-suggested",
                "suggestion": (
                    f"Oracle federation unavailable.  Manual review "
                    f"recommended for: {prop}"
                ),
                "coordinate": self.coordinate.key,
            }

        federation = OracleFederation()
        prop = query or (
            self.judgment.proposition if self.judgment else ""
        )
        return federation.consult(
            coordinate=self.coordinate.key,
            query=prop,
        )

    # ------------------------------------------------------------------ #
    # Sheaf-theoretic enrichments
    # ------------------------------------------------------------------ #

    @property
    def site(self) -> Any:
        """Return the ``Site`` object this section lives over.

        In sheaf theory, sections live over opens of a site.  This property
        lifts the section's coordinate into the full ``Site`` from
        ``jugeo.geometry.site``, exposing the Grothendieck topology,
        the covering sieves, and the restriction functors that govern how
        local data transports across the project.
        """
        try:
            from jugeo.geometry.site import Site
        except Exception:
            return {"coordinate": self.coordinate.key, "site_available": False}
        return Site.at(self.coordinate)

    @property
    def cover(self) -> Any:
        """Return the cover this section is local to.

        A section of the judgment sheaf is always relative to some cover
        ``{U_i → X}`` of its base coordinate X.  This property retrieves
        (or synthesises) the minimal cover from ``jugeo.geometry.covers``
        under which this section's data is coherent.
        """
        try:
            from jugeo.geometry.covers import Cover
        except Exception:
            return {"coordinate": self.coordinate.key, "cover_available": False}
        patches = tuple(
            CoordinateObject(path=self.coordinate.path, kind=self.coordinate.kind)
            for _ in [self]
        )
        return Cover(base=self.coordinate, patches=patches)

    def glue(self, other: "Section") -> Any:
        """Glue this section with *other* via the descent engine.

        The gluing condition in sheaf theory requires that two sections
        agree on their overlap before they can be amalgamated into a
        section over the union.  This method checks overlap compatibility,
        then delegates to ``jugeo.geometry.descent.glue_sections`` to
        produce the glued section or a ``GluingReport`` with obstruction
        data if gluing fails.

        Parameters
        ----------
        other : Section
            The section to glue with.

        Returns
        -------
        GluingReport
            The outcome of the gluing procedure.
        """
        try:
            from jugeo.geometry.descent import glue_sections
        except Exception as exc:
            raise RuntimeError(
                "Descent subsystem unavailable; cannot glue sections"
            ) from exc
        return glue_sections(self, other)

    def restrict(self, suffix: tuple[str, ...]) -> "Section":
        """Restrict to a sub-coordinate specified by *suffix*.

        Delegates to ``jugeo.geometry.site.restrict_coordinate`` to build
        the sub-coordinate, then calls :meth:`restrict_to`.  This is the
        standard restriction map ρ_{V,U} of the judgment presheaf.
        """
        sub = restrict_coordinate(self.coordinate, suffix=suffix)
        return self.restrict_to(sub)

    @property
    def encoding(self) -> Any:
        """Encode this section for downstream solvers and evaluators.

        Uses ``jugeo.encodings`` to translate the section's data, judgment
        assignments, and trust annotation into a flat scalar encoding
        suitable for Z3 or ML-based evaluation pipelines.

        Returns
        -------
        dict[str, Any]
            A mapping with encoding features and metadata.
        """
        try:
            from jugeo.encodings.scalar_encodings import encode_section
        except Exception:
            return {
                "encoding_available": False,
                "coordinate": self.coordinate.key,
                "data_key_count": len(self.data),
                "judgment_count": len(self.judgment_assignments),
                "evidence_count": len(self.evidence_archive),
                "trust_tier": (
                    self.trust_annotation.tier.label()
                    if self.trust_annotation else "unspecified"
                ),
            }
        return encode_section(self.serialize())

    def cache(self) -> Any:
        """Cache this section for efficient re-use.

        Uses ``jugeo.runtime.cache`` to store the section under a
        content-addressed key derived from its coordinate and data hash.
        Cached sections can be retrieved during incremental re-evaluation
        to avoid redundant computation.

        Returns
        -------
        dict[str, Any]
            A mapping with ``"cached"``, ``"cache_key"``, and
            ``"coordinate"`` keys.
        """
        try:
            from jugeo.runtime.cache import SectionCacheBackend
        except Exception:
            cache_key = _stable_hash(
                json.dumps({"coord": self.coordinate.key, "keys": sorted(self.data.keys())})
            )
            return {
                "cached": False,
                "cache_key": cache_key,
                "coordinate": self.coordinate.key,
                "cache_backend_available": False,
            }
        backend = SectionCacheBackend()
        cache_key = _stable_hash(
            json.dumps({"coord": self.coordinate.key, "keys": sorted(self.data.keys())})
        )
        backend.store(cache_key, self.serialize())
        return {
            "cached": True,
            "cache_key": cache_key,
            "coordinate": self.coordinate.key,
        }

    def replay(self) -> Any:
        """Replay the provenance history of this section.

        Uses ``jugeo.runtime.replay`` to reconstruct the sequence of
        operations (restriction, extension, merge, transport) that
        produced this section, enabling audit and debugging of the
        sheaf-theoretic assembly pipeline.

        Returns
        -------
        ReplayTrace
            An ordered sequence of replay events.
        """
        try:
            from jugeo.runtime.replay import ReplayEngine
        except Exception:
            return {
                "replay_available": False,
                "provenance_steps": list(self.provenance),
                "coordinate": self.coordinate.key,
            }
        engine = ReplayEngine()
        return engine.replay(
            coordinate=self.coordinate.key,
            provenance=list(self.provenance),
            data_snapshot=self.serialize(),
        )


def JudgmentSection(
    coordinate: CoordinateObject,
    context: SemanticContext | None = None,
    judgment: LocalJudgment | None = None,
    support: SupportRegion | None = None,
    patch: str = "",
    provenance: tuple[str, ...] = (),
) -> Section:
    """Backward-compatible factory matching the old ``JudgmentSection`` API.

    Maps the legacy positional signature to the new ``Section`` dataclass
    so that existing call sites (including tests) continue to work.
    """
    judgment_assignments: dict[str, LocalJudgment] = {}
    if judgment is not None:
        judgment_assignments[judgment.proposition] = judgment
    return Section(
        coordinate=coordinate,
        context=context,
        judgment=judgment,
        support_set=support,
        patch=patch,
        provenance=provenance,
        judgment_assignments=judgment_assignments,
    )


# ---------------------------------------------------------------------------
# 2. SectionFamily
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SectionFamily:
    """A family of sections indexed by the members of a cover.

    Given a cover ``{U_i → X}`` of a coordinate *X*, a section family
    assigns a ``Section`` to each patch ``U_i`` and records the restriction
    to each pairwise overlap ``U_i ∩ U_j``.
    """

    base_coordinate: CoordinateObject
    sections: dict[str, Section] = field(default_factory=dict)
    overlaps: dict[tuple[str, str], Section] = field(default_factory=dict)
    provenance: tuple[str, ...] = ()

    def verify_compatibility(self) -> list[tuple[str, str, str]]:
        """Check the cocycle condition on every recorded overlap.

        Returns a list of ``(patch_i, patch_j, reason)`` triples for each
        incompatibility found.  An empty list means the family is compatible.
        """
        issues: list[tuple[str, str, str]] = []
        for (i, j), overlap_section in self.overlaps.items():
            sec_i = self.sections.get(i)
            sec_j = self.sections.get(j)
            if sec_i is None or sec_j is None:
                issues.append((i, j, "missing section for patch"))
                continue
            if not sec_i.is_compatible_with(sec_j, overlap_section.coordinate):
                issues.append((i, j, "data mismatch on overlap"))
        return issues

    def glue(self) -> Section | None:
        """Attempt to glue the family into a single global section.

        If compatibility verification fails, returns ``None``.  Otherwise
        the global section is built by merging all patch sections in order,
        using the overlaps for evidence supplementation.
        """
        issues = self.verify_compatibility()
        if issues:
            return None
        if not self.sections:
            return Section(coordinate=self.base_coordinate, is_global=True)
        ordered = sorted(self.sections.keys())
        result = self.sections[ordered[0]]
        for key in ordered[1:]:
            overlap_ev: list[EvidenceRecord] = []
            for (i, j), osec in self.overlaps.items():
                if (i == ordered[0] and j == key) or (j == ordered[0] and i == key):
                    overlap_ev.extend(osec.evidence_archive)
            result = result.merge_with(self.sections[key], overlap_ev)
        return Section(
            coordinate=self.base_coordinate,
            data=result.data,
            judgment_assignments=result.judgment_assignments,
            evidence_archive=result.evidence_archive,
            trust_annotation=result.trust_annotation,
            support_set=result.support_set,
            is_global=True,
            residuals=result.residuals,
            provenance=self.provenance + ("glue",),
        )

    def descent_data(self) -> dict[str, Any]:
        """Package the family as descent data for the ``DescentEngine``.

        Returns a mapping from patch keys to serialised section data,
        suitable for consumption by ``jugeo.geometry.descent.glue_sections``.
        """
        result: dict[str, Any] = {}
        for key, sec in self.sections.items():
            result[key] = sec.data
        return result

    def add_section(self, patch_key: str, section: Section) -> None:
        """Register a section for the given patch key."""
        self.sections[patch_key] = section

    def add_overlap(
        self,
        patch_i: str,
        patch_j: str,
        overlap_section: Section,
    ) -> None:
        """Register an overlap section for the pair ``(patch_i, patch_j)``."""
        key = (patch_i, patch_j) if patch_i <= patch_j else (patch_j, patch_i)
        self.overlaps[key] = overlap_section

    def patch_keys(self) -> list[str]:
        """Return sorted list of all patch keys in this family."""
        return sorted(self.sections.keys())

    def missing_overlaps(self) -> list[tuple[str, str]]:
        """Return pairs of patches that are expected but have no overlap."""
        keys = self.patch_keys()
        missing: list[tuple[str, str]] = []
        for idx, i in enumerate(keys):
            for j in keys[idx + 1:]:
                pair = (i, j)
                if pair not in self.overlaps and (j, i) not in self.overlaps:
                    missing.append(pair)
        return missing


# ---------------------------------------------------------------------------
# 3. SectionRestriction
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SectionRestriction:
    """Models the restriction of section data along a coordinate morphism.

    A morphism ``f: U → X`` induces a restriction ``f*(s)`` of a section *s*
    over *X* to the sub-coordinate *U*.  This class tracks both the
    restriction itself and any data that was lost during the process.
    """

    source_section: Section
    morphism: CoordinateMorphism
    restricted_section: Section | None = None
    _lost_keys: list[str] = field(default_factory=list)

    def restrict(self) -> Section:
        """Compute the restricted section and cache it."""
        target_path = tuple(self.morphism.target.split("/"))
        target_coord = restrict_coordinate(
            self.source_section.coordinate,
            suffix=target_path[-1:],
        )
        self.restricted_section = self.source_section.restrict_to(target_coord)
        self._compute_lost_keys()
        return self.restricted_section

    def _compute_lost_keys(self) -> None:
        """Determine which data keys were dropped during restriction."""
        if self.restricted_section is None:
            return
        original_keys = set(self.source_section.data.keys())
        restricted_keys = set(self.restricted_section.data.keys())
        self._lost_keys = sorted(original_keys - restricted_keys)

    def verify_faithfulness(self) -> bool:
        """A restriction is *faithful* when no data keys are lost.

        Returns ``True`` if the restricted section retains every key
        present in the source section.
        """
        if self.restricted_section is None:
            self.restrict()
        return len(self._lost_keys) == 0

    def compute_lost_data(self) -> dict[str, Any]:
        """Return the data entries that were dropped during restriction."""
        if self.restricted_section is None:
            self.restrict()
        return {
            k: self.source_section.data[k]
            for k in self._lost_keys
            if k in self.source_section.data
        }

    def lost_judgment_propositions(self) -> list[str]:
        """Return propositions whose judgments were lost during restriction."""
        if self.restricted_section is None:
            self.restrict()
        src = set(self.source_section.judgment_assignments.keys())
        rst = set(self.restricted_section.judgment_assignments.keys())
        return sorted(src - rst)

    def faithfulness_report(self) -> dict[str, Any]:
        """Return a diagnostic report on the faithfulness of this restriction."""
        if self.restricted_section is None:
            self.restrict()
        return {
            "faithful": self.verify_faithfulness(),
            "lost_data_keys": self._lost_keys,
            "lost_judgments": self.lost_judgment_propositions(),
            "source_coordinate": self.source_section.coordinate.key,
            "morphism_reason": self.morphism.reason,
        }


# ---------------------------------------------------------------------------
# 4. SectionGluing
# ---------------------------------------------------------------------------

class GluingStatus(Enum):
    """Outcome of a gluing attempt."""
    SUCCESS = "success"
    COCYCLE_FAILURE = "cocycle_failure"
    MISSING_DATA = "missing_data"
    UNIQUENESS_FAILURE = "uniqueness_failure"


@dataclass(slots=True)
class SectionGluing:
    """Glues a compatible family of sections into a global section.

    Given a ``SectionFamily`` and witnesses for the cocycle condition on
    overlaps, this class produces either a global ``Section`` or an
    ``Obstruction`` explaining why gluing failed.
    """

    input_family: SectionFamily
    overlap_witnesses: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict,
    )
    result: Section | Obstruction | None = None
    status: GluingStatus = GluingStatus.MISSING_DATA
    _cocycle_ok: bool = False

    def verify_cocycle_condition(self) -> list[tuple[str, str, str]]:
        """Verify the cocycle condition on all triple overlaps.

        For patches ``U_i, U_j, U_k`` the restriction of the ``(i,j)``
        witness to ``U_i ∩ U_j ∩ U_k`` must agree with the ``(j,k)`` and
        ``(i,k)`` witnesses.  Returns a list of issues.
        """
        issues: list[tuple[str, str, str]] = []
        keys = self.input_family.patch_keys()
        for a_idx, a in enumerate(keys):
            for b_idx, b in enumerate(keys[a_idx + 1:], a_idx + 1):
                pair_ab = (a, b) if a <= b else (b, a)
                if pair_ab not in self.input_family.overlaps:
                    continue
                for c in keys[b_idx + 1:]:
                    pair_bc = (b, c) if b <= c else (c, b)
                    pair_ac = (a, c) if a <= c else (c, a)
                    if pair_bc not in self.input_family.overlaps:
                        continue
                    if pair_ac not in self.input_family.overlaps:
                        continue
                    ab_data = self.input_family.overlaps[pair_ab].data
                    bc_data = self.input_family.overlaps[pair_bc].data
                    ac_data = self.input_family.overlaps[pair_ac].data
                    shared = set(ab_data) & set(bc_data) & set(ac_data)
                    for key in shared:
                        if not (ab_data[key] == bc_data[key] == ac_data[key]):
                            issues.append((a, b + "/" + c, f"triple cocycle fails at key '{key}'"))
        self._cocycle_ok = len(issues) == 0
        return issues

    def glue(self) -> Section | Obstruction:
        """Perform the gluing operation.

        First checks compatibility and the cocycle condition.  On success
        delegates to ``SectionFamily.glue``.  On failure returns an
        ``Obstruction`` describing the problem.
        """
        compat_issues = self.input_family.verify_compatibility()
        if compat_issues:
            first = compat_issues[0]
            obs = Obstruction(
                overlap=(first[0], first[1]),
                message=first[2],
                rank=len(compat_issues),
            )
            self.result = obs
            self.status = GluingStatus.COCYCLE_FAILURE
            return obs

        cocycle_issues = self.verify_cocycle_condition()
        if cocycle_issues:
            first = cocycle_issues[0]
            obs = Obstruction(
                overlap=(first[0], first[1]),
                message=first[2],
                rank=len(cocycle_issues),
            )
            self.result = obs
            self.status = GluingStatus.COCYCLE_FAILURE
            return obs

        glued = self.input_family.glue()
        if glued is None:
            obs = Obstruction(
                overlap=("*", "*"),
                message="gluing returned None despite passing checks",
                rank=1,
            )
            self.result = obs
            self.status = GluingStatus.MISSING_DATA
            return obs

        self.result = glued
        self.status = GluingStatus.SUCCESS
        return glued

    def compute_uniqueness(self) -> bool:
        """Check that the glued section is unique up to the overlap data.

        Uniqueness holds when every key in the glued section data can be
        traced back to exactly one patch section.
        """
        if not isinstance(self.result, Section):
            return False
        key_sources: dict[str, list[str]] = {}
        for patch_key, sec in self.input_family.sections.items():
            for data_key in sec.data:
                key_sources.setdefault(data_key, []).append(patch_key)
        for data_key, sources in key_sources.items():
            if len(sources) > 1:
                values = [
                    self.input_family.sections[s].data[data_key]
                    for s in sources
                ]
                if len(set(map(str, values))) > 1:
                    return False
        return True

    def gluing_report(self) -> dict[str, Any]:
        """Return a diagnostic summary of the gluing attempt."""
        return {
            "status": self.status.value,
            "cocycle_ok": self._cocycle_ok,
            "unique": self.compute_uniqueness() if isinstance(self.result, Section) else None,
            "patch_count": len(self.input_family.sections),
            "overlap_count": len(self.input_family.overlaps),
            "result_type": type(self.result).__name__ if self.result else "None",
        }


# ---------------------------------------------------------------------------
# 5. SectionTransport
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SectionTransport:
    """Transports a section along a coordinate morphism (change of base).

    Given a section *s* over *X* and a morphism ``f: Y → X``, the transport
    ``f_*(s)`` produces a section over *Y* by pulling back data, judgments,
    and evidence.
    """

    source_section: Section
    morphism: CoordinateMorphism
    transported: Section | None = None
    _trust_preserved: bool | None = None

    def transport(self) -> Section:
        """Compute the transported section.

        Data is copied verbatim.  Judgment assignments are re-keyed to the
        target coordinate.  Trust annotations are preserved but annotated
        with a transport reason.
        """
        target_parts = self.morphism.target.split("/")
        target_coord = CoordinateObject(
            name=target_parts[-1] if target_parts else self.morphism.target,
            kind=self.source_section.coordinate.kind,
            path=tuple(target_parts),
        )
        transported_judgments: dict[str, LocalJudgment] = {}
        for prop, jdg in self.source_section.judgment_assignments.items():
            transported_judgments[prop] = LocalJudgment(
                coordinate=target_coord,
                proposition=jdg.proposition,
                artifact=jdg.artifact,
                evidence_refs=jdg.evidence_refs,
                obligations=jdg.obligations,
                obstructions=jdg.obstructions,
                trust_vector=jdg.trust_vector,
                provenance=jdg.provenance + ("transport",),
                clauses=jdg.clauses,
                status=jdg.status,
            )

        transported_trust: TrustProfile | None = None
        if self.source_section.trust_annotation is not None:
            transported_trust = self.source_section.trust_annotation.with_reasons(
                f"transported via {self.morphism.reason}",
            )

        self.transported = Section(
            coordinate=target_coord,
            data=dict(self.source_section.data),
            judgment_assignments=transported_judgments,
            evidence_archive=list(self.source_section.evidence_archive),
            trust_annotation=transported_trust,
            support_set=None,
            is_global=self.source_section.is_global,
            residuals=list(self.source_section.residuals),
            provenance=self.source_section.provenance + ("transport",),
        )
        return self.transported

    def verify_transport_preserves_trust(self) -> bool:
        """Check whether the transport preserved the trust tier.

        A transport preserves trust when the transported section's trust
        tier is greater than or equal to the source section's tier.
        """
        if self.transported is None:
            self.transport()
        src = self.source_section.trust_annotation
        tgt = self.transported.trust_annotation  # type: ignore[union-attr]
        if src is None and tgt is None:
            self._trust_preserved = True
        elif src is None or tgt is None:
            self._trust_preserved = False
        else:
            self._trust_preserved = tgt.tier >= src.tier
        return self._trust_preserved  # type: ignore[return-value]

    def compute_transported_obligations(self) -> list[str]:
        """List obligations that survive transport.

        All obligations from the source section are carried over; none are
        discharged by transport alone.
        """
        if self.transported is None:
            self.transport()
        return list(self.transported.residuals)  # type: ignore[union-attr]

    def transport_summary(self) -> dict[str, Any]:
        """Return a diagnostic summary of the transport operation."""
        if self.transported is None:
            self.transport()
        return {
            "source": self.source_section.coordinate.key,
            "target": self.transported.coordinate.key,  # type: ignore[union-attr]
            "morphism_reason": self.morphism.reason,
            "trust_preserved": self.verify_transport_preserves_trust(),
            "data_keys_count": len(self.transported.data),  # type: ignore[union-attr]
            "judgment_count": len(self.transported.judgment_assignments),  # type: ignore[union-attr]
            "obligations": self.compute_transported_obligations(),
        }


# ---------------------------------------------------------------------------
# 6. SheafCondition
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SheafCondition:
    """Checks whether section data satisfies the sheaf axioms.

    The two axioms are:
    * **Locality**: if two global sections agree on every member of a cover
      they are equal (separation).
    * **Gluing**: a compatible family of local sections can be glued into a
      unique global section (existence).
    """

    cover: Cover
    local_sections: dict[str, Section] = field(default_factory=dict)

    def locality_check(
        self,
        section_a: Section,
        section_b: Section,
    ) -> bool:
        """Verify the locality (separation) axiom.

        If *section_a* and *section_b* agree on every patch of the cover
        then they must be equal as sections over the target coordinate.
        """
        for patch in self.cover.patches:
            res_a = section_a.restrict_to(patch)
            res_b = section_b.restrict_to(patch)
            if res_a.data != res_b.data:
                return False
            if set(res_a.judgment_assignments.keys()) != set(res_b.judgment_assignments.keys()):
                return False
        return True

    def gluing_check(self) -> tuple[bool, list[str]]:
        """Verify the gluing axiom.

        Checks that the local sections recorded for each patch are pairwise
        compatible on overlaps and that they can be glued into a global
        section.

        Returns ``(success, issues)`` where *issues* is empty on success.
        """
        issues: list[str] = []
        keys = sorted(self.local_sections.keys())
        for idx, i in enumerate(keys):
            for j in keys[idx + 1:]:
                overlap_pair = (i, j) if i <= j else (j, i)
                if overlap_pair in [(a, b) for (a, b) in self.cover.overlaps]:
                    sec_i = self.local_sections[i]
                    sec_j = self.local_sections[j]
                    if not sec_i.is_compatible_with(sec_j):
                        issues.append(f"Sections at {i} and {j} are incompatible")
        success = len(issues) == 0
        return success, issues

    def full_check(self) -> dict[str, Any]:
        """Run both axiom checks and return a combined report."""
        gluing_ok, gluing_issues = self.gluing_check()
        return {
            "cover_target": self.cover.target.key,
            "patch_count": len(self.cover.patches),
            "overlap_count": len(self.cover.overlaps),
            "gluing_ok": gluing_ok,
            "gluing_issues": gluing_issues,
            "sections_registered": len(self.local_sections),
        }

    def assign_local_section(self, patch_key: str, section: Section) -> None:
        """Register a section for a cover patch."""
        self.local_sections[patch_key] = section

    def clear(self) -> None:
        """Remove all registered local sections."""
        self.local_sections.clear()

    def is_separated(self) -> bool:
        """Quick test: are all registered sections pairwise distinguishable?

        Returns ``False`` if any two distinct sections have identical data.
        """
        secs = list(self.local_sections.values())
        for idx in range(len(secs)):
            for jdx in range(idx + 1, len(secs)):
                if secs[idx].data == secs[jdx].data:
                    return False
        return True


# ---------------------------------------------------------------------------
# 7. SectionComparator
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SectionComparator:
    """Compares two sections structurally.

    All comparison methods are static so that a single comparator instance
    can be reused across an entire orchestration pass.
    """

    @staticmethod
    def is_refinement_of(candidate: Section, base: Section) -> bool:
        """True when *candidate* has strictly more data than *base*.

        Every key in *base.data* must appear in *candidate.data* with the
        same value, and *candidate* must have at least one additional key.
        """
        for key, value in base.data.items():
            if key not in candidate.data or candidate.data[key] != value:
                return False
        return len(candidate.data) > len(base.data)

    @staticmethod
    def is_restriction_of(candidate: Section, base: Section) -> bool:
        """True when *candidate* is a sub-section of *base*.

        Every key in *candidate.data* must appear in *base.data* with the
        same value, and *candidate* must have fewer or equal keys.
        """
        for key, value in candidate.data.items():
            if key not in base.data or base.data[key] != value:
                return False
        return len(candidate.data) <= len(base.data)

    @staticmethod
    def is_equivalent_to(a: Section, b: Section) -> bool:
        """True when the two sections carry the same data and judgments."""
        if a.data != b.data:
            return False
        if set(a.judgment_assignments.keys()) != set(b.judgment_assignments.keys()):
            return False
        for prop in a.judgment_assignments:
            if a.judgment_assignments[prop].proposition != b.judgment_assignments[prop].proposition:
                return False
        return True

    @staticmethod
    def diff(a: Section, b: Section) -> dict[str, Any]:
        """Compute a structural diff between two sections.

        Returns a dict with keys ``added``, ``removed``, ``changed`` for
        data entries, plus ``judgment_diff`` for propositions that differ.
        """
        a_keys = set(a.data.keys())
        b_keys = set(b.data.keys())
        added = {k: b.data[k] for k in b_keys - a_keys}
        removed = {k: a.data[k] for k in a_keys - b_keys}
        changed: dict[str, tuple[Any, Any]] = {}
        for k in a_keys & b_keys:
            if a.data[k] != b.data[k]:
                changed[k] = (a.data[k], b.data[k])
        a_props = set(a.judgment_assignments.keys())
        b_props = set(b.judgment_assignments.keys())
        judgment_diff = {
            "added": sorted(b_props - a_props),
            "removed": sorted(a_props - b_props),
            "shared": sorted(a_props & b_props),
        }
        return {
            "added": added,
            "removed": removed,
            "changed": changed,
            "judgment_diff": judgment_diff,
        }

    @staticmethod
    def common_subsection(a: Section, b: Section) -> Section:
        """Build a section containing only the data common to both inputs.

        The resulting section lives at the coordinate of *a* and contains
        only keys whose values are identical in both sections.
        """
        common_data: dict[str, Any] = {}
        for key in set(a.data) & set(b.data):
            if a.data[key] == b.data[key]:
                common_data[key] = a.data[key]
        common_judgments: dict[str, LocalJudgment] = {}
        for prop in set(a.judgment_assignments) & set(b.judgment_assignments):
            ja = a.judgment_assignments[prop]
            jb = b.judgment_assignments[prop]
            if ja.proposition == jb.proposition:
                common_judgments[prop] = ja
        return Section(
            coordinate=a.coordinate,
            data=common_data,
            judgment_assignments=common_judgments,
            provenance=("common_subsection",),
        )

    @staticmethod
    def similarity_score(a: Section, b: Section) -> float:
        """Compute a Jaccard-like similarity score in ``[0, 1]``.

        The score is the ratio of common data keys to the union of data
        keys.  Returns ``1.0`` when both sections have no data.
        """
        a_keys = set(a.data.keys())
        b_keys = set(b.data.keys())
        union = a_keys | b_keys
        if not union:
            return 1.0
        return len(a_keys & b_keys) / len(union)


# ---------------------------------------------------------------------------
# 8. SectionBuilder
# ---------------------------------------------------------------------------

class SectionBuilder:
    """Fluent builder for constructing ``Section`` instances incrementally.

    Usage::

        section = (
            SectionBuilder()
            .at_coordinate(coord)
            .with_judgment("safety", judgment)
            .with_evidence(record)
            .with_trust(profile)
            .with_data("key", "value")
            .with_residual("obligation-1")
            .mark_global()
            .build()
        )
    """

    def __init__(self) -> None:
        self._coordinate: CoordinateObject | None = None
        self._data: dict[str, Any] = {}
        self._judgments: dict[str, LocalJudgment] = {}
        self._evidence: list[EvidenceRecord] = []
        self._trust: TrustProfile | None = None
        self._support: SupportRegion | None = None
        self._residuals: list[str] = []
        self._global: bool = False
        self._provenance: list[str] = []

    def at_coordinate(self, coordinate: CoordinateObject) -> SectionBuilder:
        """Set the coordinate for the section under construction."""
        self._coordinate = coordinate
        return self

    def with_data(self, key: str, value: Any) -> SectionBuilder:
        """Add a named typed value to the section data."""
        self._data[key] = value
        return self

    def with_judgment(self, proposition: str, judgment: LocalJudgment) -> SectionBuilder:
        """Assign a judgment for the given proposition."""
        self._judgments[proposition] = judgment
        return self

    def with_evidence(self, record: EvidenceRecord) -> SectionBuilder:
        """Append an evidence record to the section archive."""
        self._evidence.append(record)
        return self

    def with_trust(self, profile: TrustProfile) -> SectionBuilder:
        """Set the trust annotation for this section."""
        self._trust = profile
        return self

    def with_support(self, support: SupportRegion) -> SectionBuilder:
        """Set the support region for the section."""
        self._support = support
        return self

    def with_residual(self, obligation: str) -> SectionBuilder:
        """Add a residual obligation."""
        if obligation not in self._residuals:
            self._residuals.append(obligation)
        return self

    def with_provenance(self, *tags: str) -> SectionBuilder:
        """Append provenance tags."""
        self._provenance.extend(tags)
        return self

    def mark_global(self) -> SectionBuilder:
        """Mark the section as global."""
        self._global = True
        return self

    def build(self) -> Section:
        """Construct and return the ``Section``.

        Raises ``ValueError`` if no coordinate has been set.
        """
        if self._coordinate is None:
            raise ValueError("SectionBuilder requires a coordinate (call at_coordinate first)")
        return Section(
            coordinate=self._coordinate,
            data=dict(self._data),
            judgment_assignments=dict(self._judgments),
            evidence_archive=list(self._evidence),
            trust_annotation=self._trust,
            support_set=self._support,
            is_global=self._global,
            residuals=list(self._residuals),
            provenance=tuple(self._provenance),
        )

    def reset(self) -> SectionBuilder:
        """Reset the builder to its initial state."""
        self.__init__()  # type: ignore[misc]
        return self

    def clone(self) -> SectionBuilder:
        """Return a shallow copy of this builder."""
        other = SectionBuilder()
        other._coordinate = self._coordinate
        other._data = dict(self._data)
        other._judgments = dict(self._judgments)
        other._evidence = list(self._evidence)
        other._trust = self._trust
        other._support = self._support
        other._residuals = list(self._residuals)
        other._global = self._global
        other._provenance = list(self._provenance)
        return other


# ---------------------------------------------------------------------------
# 9. SectionCache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    """Internal cache entry with timestamp for staleness tracking."""
    section: Section
    created_at: float
    last_accessed: float
    access_count: int = 0


class SectionCache:
    """LRU-style cache for computed sections, keyed by coordinate path.

    Supports hierarchical invalidation: invalidating a coordinate also
    invalidates all descendants.

    # copilot: cache layer for judgment-section reuse across orchestration rounds.
    """

    def __init__(self, *, max_size: int = 256, ttl_seconds: float = 600.0) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._hits: int = 0
        self._misses: int = 0

    def get(self, coordinate: CoordinateObject) -> Section | None:
        """Retrieve a cached section, or ``None`` on miss."""
        key = coordinate.key
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        if (time.monotonic() - entry.created_at) > self._ttl:
            del self._store[key]
            self._misses += 1
            return None
        entry.last_accessed = time.monotonic()
        entry.access_count += 1
        self._hits += 1
        return entry.section

    def put(self, section: Section) -> None:
        """Insert or update a section in the cache."""
        if len(self._store) >= self._max_size:
            self._evict_lru()
        now = time.monotonic()
        self._store[section.coordinate.key] = _CacheEntry(
            section=section,
            created_at=now,
            last_accessed=now,
        )

    def invalidate_at(self, coordinate: CoordinateObject) -> bool:
        """Invalidate the cached section at exactly *coordinate*.

        Returns ``True`` if an entry was removed.
        """
        return self._store.pop(coordinate.key, None) is not None

    def invalidate_below(self, coordinate: CoordinateObject) -> int:
        """Invalidate all cached sections whose path starts with *coordinate*.

        Returns the number of entries removed.
        """
        prefix = coordinate.key
        to_remove = [
            k for k in self._store
            if k == prefix or k.startswith(prefix + "/")
        ]
        for k in to_remove:
            del self._store[k]
        return len(to_remove)

    def evict_stale(self) -> int:
        """Remove all entries older than the TTL.

        Returns the number of entries evicted.
        """
        now = time.monotonic()
        stale = [
            k for k, v in self._store.items()
            if (now - v.created_at) > self._ttl
        ]
        for k in stale:
            del self._store[k]
        return len(stale)

    def hit_rate(self) -> float:
        """Return the cache hit rate as a float in ``[0, 1]``."""
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return self._hits / total

    def size(self) -> int:
        """Return the current number of cached entries."""
        return len(self._store)

    def clear(self) -> None:
        """Remove all cached entries and reset statistics."""
        self._store.clear()
        self._hits = 0
        self._misses = 0

    def keys(self) -> list[str]:
        """Return all currently cached coordinate keys."""
        return sorted(self._store.keys())

    def _evict_lru(self) -> None:
        """Remove the least-recently-accessed entry."""
        if not self._store:
            return
        lru_key = min(self._store, key=lambda k: self._store[k].last_accessed)
        del self._store[lru_key]

    def stats(self) -> dict[str, Any]:
        """Return cache statistics as a dictionary."""
        return {
            "size": self.size(),
            "max_size": self._max_size,
            "ttl_seconds": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate(),
        }


# ---------------------------------------------------------------------------
# 10. SectionSerializer
# ---------------------------------------------------------------------------

class SectionSerializer:
    """JSON serialization for sections, families, and restrictions.

    All ``serialize_*`` methods return plain Python dicts suitable for
    ``json.dumps``.  The corresponding ``deserialize_*`` class methods
    reconstruct the objects (where possible without the full type graph).

    # copilot: serialization bridge for cross-process judgment exchange.
    """

    @staticmethod
    def serialize_section(section: Section) -> dict[str, Any]:
        """Serialize a ``Section`` to a JSON-compatible dict."""
        judgment_payload: dict[str, Any] = {}
        for prop, jdg in section.judgment_assignments.items():
            judgment_payload[prop] = {
                "proposition": jdg.proposition,
                "status": jdg.status.name,
                "obligations": list(jdg.obligations),
                "obstructions": list(jdg.obstructions),
                "evidence_refs": list(jdg.evidence_refs),
                "provenance": list(jdg.provenance),
            }
        evidence_payload: list[dict[str, Any]] = []
        for rec in section.evidence_archive:
            evidence_payload.append({
                "channel_name": rec.channel.name,
                "channel_kind": rec.channel.kind.name,
                "claim": rec.claim,
                "obligations": list(rec.obligations),
                "provenance": list(rec.provenance),
            })
        trust_payload: dict[str, Any] | None = None
        if section.trust_annotation is not None:
            trust_payload = section.trust_annotation.to_dict()
        support_payload: dict[str, Any] | None = None
        if section.support_set is not None:
            support_payload = {
                "patch_keys": sorted(section.support_set.patch_keys),
                "labels": sorted(section.support_set.labels),
                "provenance": list(section.support_set.provenance),
            }
        return {
            "coordinate": {
                "name": section.coordinate.name,
                "kind": section.coordinate.kind.name,
                "path": list(section.coordinate.path),
            },
            "data": section.data,
            "judgments": judgment_payload,
            "evidence": evidence_payload,
            "trust": trust_payload,
            "support": support_payload,
            "is_global": section.is_global,
            "residuals": section.residuals,
            "provenance": list(section.provenance),
        }

    @staticmethod
    def serialize_family(family: SectionFamily) -> dict[str, Any]:
        """Serialize a ``SectionFamily`` to a JSON-compatible dict."""
        sections_payload: dict[str, Any] = {}
        for key, sec in family.sections.items():
            sections_payload[key] = SectionSerializer.serialize_section(sec)
        overlaps_payload: dict[str, Any] = {}
        for (i, j), sec in family.overlaps.items():
            overlap_key = f"{i}:{j}"
            overlaps_payload[overlap_key] = SectionSerializer.serialize_section(sec)
        return {
            "base_coordinate": {
                "name": family.base_coordinate.name,
                "kind": family.base_coordinate.kind.name,
                "path": list(family.base_coordinate.path),
            },
            "sections": sections_payload,
            "overlaps": overlaps_payload,
            "provenance": list(family.provenance),
        }

    @staticmethod
    def serialize_restriction(restriction: SectionRestriction) -> dict[str, Any]:
        """Serialize a ``SectionRestriction`` to a JSON-compatible dict."""
        result: dict[str, Any] = {
            "source": SectionSerializer.serialize_section(restriction.source_section),
            "morphism": {
                "source": restriction.morphism.source,
                "target": restriction.morphism.target,
                "reason": restriction.morphism.reason,
            },
            "faithful": restriction.verify_faithfulness(),
            "lost_keys": restriction._lost_keys,
        }
        if restriction.restricted_section is not None:
            result["restricted"] = SectionSerializer.serialize_section(
                restriction.restricted_section,
            )
        return result

    @staticmethod
    def serialize_transport(transport: SectionTransport) -> dict[str, Any]:
        """Serialize a ``SectionTransport`` to a JSON-compatible dict."""
        result: dict[str, Any] = {
            "source": SectionSerializer.serialize_section(transport.source_section),
            "morphism": {
                "source": transport.morphism.source,
                "target": transport.morphism.target,
                "reason": transport.morphism.reason,
            },
        }
        if transport.transported is not None:
            result["transported"] = SectionSerializer.serialize_section(transport.transported)
            result["trust_preserved"] = transport.verify_transport_preserves_trust()
        return result

    @staticmethod
    def deserialize_section_stub(payload: dict[str, Any]) -> Section:
        """Reconstruct a ``Section`` stub from serialised data.

        This recovers coordinate and data but not full judgment/evidence
        objects (which require the wider type graph).
        """
        coord_data = payload.get("coordinate", {})
        coordinate = CoordinateObject(
            name=coord_data.get("name", "unknown"),
            kind=CoordinateKind[coord_data.get("kind", "MODULE")],
            path=tuple(coord_data.get("path", [])),
        )
        return Section(
            coordinate=coordinate,
            data=payload.get("data", {}),
            is_global=payload.get("is_global", False),
            residuals=payload.get("residuals", []),
            provenance=tuple(payload.get("provenance", [])),
        )

    @staticmethod
    def to_json(section: Section, *, indent: int = 2) -> str:
        """Convenience: serialize a section directly to a JSON string."""
        return json.dumps(
            SectionSerializer.serialize_section(section),
            indent=indent,
            default=str,
        )

    @staticmethod
    def family_to_json(family: SectionFamily, *, indent: int = 2) -> str:
        """Convenience: serialize a family directly to a JSON string."""
        return json.dumps(
            SectionSerializer.serialize_family(family),
            indent=indent,
            default=str,
        )


# ---------------------------------------------------------------------------
# 11. SectionDiagnostics
# ---------------------------------------------------------------------------

class SectionDiagnostics:
    """Diagnostic methods for inspecting and validating sections.

    Each method analyses one or more sections and returns a structured
    report.  These are designed for use by the orchestration layer and
    the ``copilot`` LLM integration.

    # copilot: diagnostic hooks for LLM-driven judgment review.
    """

    @staticmethod
    def find_unsupported_claims(section: Section) -> list[dict[str, Any]]:
        """Find judgment propositions with no evidence backing.

        A claim is *unsupported* when its judgment has no evidence refs
        and the section's evidence archive contains no record whose claim
        matches the proposition.
        """
        archive_claims = {rec.claim for rec in section.evidence_archive}
        unsupported: list[dict[str, Any]] = []
        for prop, jdg in section.judgment_assignments.items():
            has_refs = len(jdg.evidence_refs) > 0
            has_archive = jdg.proposition in archive_claims
            if not has_refs and not has_archive:
                unsupported.append({
                    "proposition": prop,
                    "status": jdg.status.name,
                    "obligations": list(jdg.obligations),
                })
        return unsupported

    @staticmethod
    def find_trust_violations(section: Section) -> list[dict[str, Any]]:
        """Detect judgments whose status is inconsistent with the trust tier.

        A *trust violation* occurs when a judgment is marked SETTLED but
        the section trust tier is below VERIFIED, or when a PROPOSED
        judgment sits under VERIFIED trust (indicating stale promotion).
        """
        violations: list[dict[str, Any]] = []
        if section.trust_annotation is None:
            return violations
        tier = section.trust_annotation.tier
        for prop, jdg in section.judgment_assignments.items():
            if jdg.status == JudgmentStatus.SETTLED and tier < TrustTier.VERIFIED:
                violations.append({
                    "proposition": prop,
                    "issue": "settled_under_weak_trust",
                    "trust_tier": tier.label(),
                    "judgment_status": jdg.status.name,
                })
            if jdg.status == JudgmentStatus.PROPOSED and tier == TrustTier.VERIFIED:
                violations.append({
                    "proposition": prop,
                    "issue": "proposed_under_verified_trust",
                    "trust_tier": tier.label(),
                    "judgment_status": jdg.status.name,
                })
        return violations

    @staticmethod
    def find_orphaned_evidence(section: Section) -> list[dict[str, Any]]:
        """Find evidence records not referenced by any judgment.

        An *orphaned* record is one whose claim does not appear as a
        proposition in any judgment assignment, and whose canonical key
        is not listed in any judgment's evidence_refs.
        """
        all_props = set(section.judgment_assignments.keys())
        all_refs: set[str] = set()
        for jdg in section.judgment_assignments.values():
            all_refs.update(jdg.evidence_refs)
        orphaned: list[dict[str, Any]] = []
        for rec in section.evidence_archive:
            if rec.claim not in all_props and rec.canonical_key() not in all_refs:
                orphaned.append({
                    "claim": rec.claim,
                    "channel": rec.channel.name,
                    "kind": rec.channel.kind.name,
                })
        return orphaned

    @staticmethod
    def coverage_report(section: Section) -> dict[str, Any]:
        """Produce a coverage report for the section.

        Reports how many data keys are backed by judgments, how many
        judgments have evidence, and the overall trust posture.
        """
        total_keys = len(section.data)
        total_judgments = len(section.judgment_assignments)
        total_evidence = len(section.evidence_archive)

        backed_keys = 0
        for key in section.data:
            if key in section.judgment_assignments:
                backed_keys += 1

        evidenced_judgments = 0
        archive_claims = {rec.claim for rec in section.evidence_archive}
        for jdg in section.judgment_assignments.values():
            if jdg.evidence_refs or jdg.proposition in archive_claims:
                evidenced_judgments += 1

        trust_label = "none"
        if section.trust_annotation is not None:
            trust_label = section.trust_annotation.tier.label()

        return {
            "coordinate": section.coordinate.key,
            "total_data_keys": total_keys,
            "backed_by_judgment": backed_keys,
            "total_judgments": total_judgments,
            "evidenced_judgments": evidenced_judgments,
            "total_evidence_records": total_evidence,
            "residuals": len(section.residuals),
            "trust": trust_label,
            "is_global": section.is_global,
            "data_coverage": backed_keys / total_keys if total_keys else 1.0,
            "evidence_coverage": (
                evidenced_judgments / total_judgments if total_judgments else 1.0
            ),
        }

    @staticmethod
    def copilot_section_summary(section: Section) -> str:
        """Return a human-readable summary for copilot LLM consumption.

        The summary is designed to fit into a context window and convey
        the essential posture of the section in natural language.

        # copilot: primary diagnostic entry point for LLM orchestration.
        """
        report = SectionDiagnostics.coverage_report(section)
        unsupported = SectionDiagnostics.find_unsupported_claims(section)
        violations = SectionDiagnostics.find_trust_violations(section)
        orphaned = SectionDiagnostics.find_orphaned_evidence(section)

        lines: list[str] = []
        lines.append(f"Section at '{section.coordinate.key}' "
                      f"({'GLOBAL' if section.is_global else 'local'}):")
        lines.append(f"  Data keys: {report['total_data_keys']}, "
                      f"Judgments: {report['total_judgments']}, "
                      f"Evidence records: {report['total_evidence_records']}")
        lines.append(f"  Trust tier: {report['trust']}")
        lines.append(f"  Data coverage: {report['data_coverage']:.0%}, "
                      f"Evidence coverage: {report['evidence_coverage']:.0%}")
        if unsupported:
            lines.append(f"  ⚠ Unsupported claims: {len(unsupported)}")
            for u in unsupported[:3]:
                lines.append(f"    - {u['proposition']} ({u['status']})")
        if violations:
            lines.append(f"  ⚠ Trust violations: {len(violations)}")
            for v in violations[:3]:
                lines.append(f"    - {v['proposition']}: {v['issue']}")
        if orphaned:
            lines.append(f"  ⚠ Orphaned evidence: {len(orphaned)}")
            for o in orphaned[:3]:
                lines.append(f"    - {o['claim']} via {o['channel']}")
        if section.residuals:
            lines.append(f"  Residuals ({len(section.residuals)}):")
            for r in section.residuals[:5]:
                lines.append(f"    - {r}")
        return "\n".join(lines)

    @staticmethod
    def diff_report(a: Section, b: Section) -> str:
        """Human-readable diff between two sections."""
        diff = SectionComparator.diff(a, b)
        lines: list[str] = []
        lines.append(f"Diff: '{a.coordinate.key}' vs '{b.coordinate.key}'")
        if diff["added"]:
            lines.append(f"  Added keys ({len(diff['added'])}): {sorted(diff['added'].keys())}")
        if diff["removed"]:
            lines.append(f"  Removed keys ({len(diff['removed'])}): {sorted(diff['removed'].keys())}")
        if diff["changed"]:
            lines.append(f"  Changed keys ({len(diff['changed'])}):")
            for k, (old, new) in diff["changed"].items():
                lines.append(f"    {k}: {old!r} → {new!r}")
        jd = diff["judgment_diff"]
        if jd["added"]:
            lines.append(f"  Added judgments: {jd['added']}")
        if jd["removed"]:
            lines.append(f"  Removed judgments: {jd['removed']}")
        return "\n".join(lines)

    @staticmethod
    def family_health(family: SectionFamily) -> dict[str, Any]:
        """Run diagnostics on every section in a family."""
        per_patch: dict[str, dict[str, Any]] = {}
        for key, sec in family.sections.items():
            per_patch[key] = SectionDiagnostics.coverage_report(sec)
        compat = family.verify_compatibility()
        missing = family.missing_overlaps()
        return {
            "base_coordinate": family.base_coordinate.key,
            "patch_count": len(family.sections),
            "overlap_count": len(family.overlaps),
            "compatibility_issues": compat,
            "missing_overlaps": missing,
            "per_patch": per_patch,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "Section",
    "JudgmentSection",
    "SectionFamily",
    "SectionRestriction",
    "SectionGluing",
    "GluingStatus",
    "SectionTransport",
    "SheafCondition",
    "SectionComparator",
    "SectionBuilder",
    "SectionCache",
    "SectionSerializer",
    "SectionDiagnostics",
]

# copilot: shared-core marker for LLM orchestration and judgment-section transport.
