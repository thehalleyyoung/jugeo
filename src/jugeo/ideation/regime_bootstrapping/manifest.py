"""
Manifest layer for the JuGeo ``regime_bootstrapping`` package.

copilot: shared-core marker

Theory reference: theory2.tex Ch55 — Regime Bootstrapping

A *bootstrapping manifest* is the authoritative, versioned record of a
completed (or in-progress) regime-bootstrapping run.  It binds together
the domain formation, type constructors, obstruction fields, and evidence
references that collectively justify the production of a particular regime.

--------------------------------------------------------------------------------
THEORETICAL BACKGROUND  (Ch55 §7 – Manifest Semantics)
--------------------------------------------------------------------------------

In Ch55 the manifest plays the role of a *certificate*: given a manifest M
for a regime R, any downstream consumer can verify that:

  1. The domain formation D referenced in M was obtained from valid generators
     and relations (§7.1 – Domain Certificate).

  2. Each type constructor T_i in M was applied with correct arity and in a
     valid topological order relative to the domain partitions (§7.2 –
     Constructor Certificate).

  3. Every obstruction field Obs_j recorded in M was either:
       (a) fully resolved by a subsequent type constructor, or
       (b) classified as non-blocking (severity < threshold) and explicitly
           acknowledged (§7.3 – Obstruction Certificate).

  4. The evidence references E_k in M collectively provide sufficient
     external justification for the regime (§7.4 – Evidence Coverage).

The ``coverage_score`` method approximates the §7.4 coverage criterion as a
single normalised float, combining evidence count, trust level, and obstruction
resolution rate.

A manifest begins in an *unfinalized* state while bootstrapping is in progress.
Once ``finalize()`` is called, the manifest is frozen (``finalized_at`` is set)
and cannot be modified further.  Callers that need to extend a finalized
manifest must create a new one via ``merge_with()``.

--------------------------------------------------------------------------------
BUILDER PATTERN
--------------------------------------------------------------------------------

The preferred way to construct a manifest is via ``BootstrappingManifestBuilder``:

  >>> builder = BootstrappingManifestBuilder()
  >>> builder.with_plan_id("plan-abc")
  >>> builder.with_domain_formation(domain)
  >>> builder.with_type_constructors([ctor1, ctor2])
  >>> builder.add_obstruction_field(obs)
  >>> builder.add_evidence_ref("ev://ref/001")
  >>> builder.set_trust_level(0.85)
  >>> manifest = builder.build()

The builder performs all validation before constructing the manifest,
raising ``ValueError`` if required fields are missing or inconsistent.

The free function ``build_bootstrapping_manifest()`` is a thin wrapper
around the builder for callers that prefer a functional style.

--------------------------------------------------------------------------------
VALIDATION
--------------------------------------------------------------------------------

``ManifestValidationResult`` captures the outcome of validating a manifest:

  * ``is_valid``  — overall pass/fail
  * ``errors``    — list of error strings that must be fixed for validity
  * ``warnings``  — list of advisory strings (do not affect validity)
  * ``score``     — a normalised quality score in [0, 1]

Call ``ManifestValidationResult.raise_if_invalid()`` to convert errors into
a ``ValueError`` exception, which is useful in assertion-style validation.

--------------------------------------------------------------------------------
MODULE ORGANISATION
--------------------------------------------------------------------------------

  Helpers         — _utcnow, _uid, _clamp, _make_manifest_id, _make_ref_id
  Models          — ManifestValidationResult, RegimeBootstrappingManifest
  Builder         — BootstrappingManifestBuilder
  Free functions  — build_bootstrapping_manifest, _validate_manifest_fields

--------------------------------------------------------------------------------
DEPENDENCIES
--------------------------------------------------------------------------------

  jugeo.ideation.regime_bootstrapping.models is always available (same package).
  All other cross-module imports are guarded with try/except.
"""

from __future__ import annotations

import math
import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "RegimeBootstrappingManifest",
    "BootstrappingManifestBuilder",
    "ManifestValidationResult",
    "build_bootstrapping_manifest",
    "_validate_manifest_fields",
    "_utcnow",
    "_uid",
    "_clamp",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
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
    from jugeo.ideation.regime_bootstrapping.models import (
        DomainFormation,
        TypeConstructor,
        ObstructionField,
        BootstrapStatus,
        MODELS_SCHEMA_VERSION,
        _utcnow as _models_utcnow,
    )
except Exception:
    DomainFormation = None  # type: ignore[assignment,misc]
    TypeConstructor = None  # type: ignore[assignment,misc]
    ObstructionField = None  # type: ignore[assignment,misc]
    BootstrapStatus = None  # type: ignore[assignment,misc]
    MODELS_SCHEMA_VERSION = "1.0.0"
    _models_utcnow = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Schema version for manifest objects — increment when fields change.
MANIFEST_SCHEMA_VERSION: str = "1.0.0"

#: Minimum number of evidence references for a manifest to be considered
#: well-evidenced.  Manifests with fewer refs receive a coverage warning.
MIN_EVIDENCE_REFS_FOR_COVERAGE: int = 2

#: Trust level below which the manifest validation emits a warning.
TRUST_LEVEL_WARNING_THRESHOLD: float = 0.4

#: Trust level below which the manifest validation emits an error.
TRUST_LEVEL_ERROR_THRESHOLD: float = 0.1

#: Weight of evidence-count contribution to coverage_score.
COVERAGE_WEIGHT_EVIDENCE: float = 0.4

#: Weight of trust-level contribution to coverage_score.
COVERAGE_WEIGHT_TRUST: float = 0.4

#: Weight of obstruction-resolution-rate contribution to coverage_score.
COVERAGE_WEIGHT_RESOLUTION: float = 0.2

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ManifestId = str
EvidenceRef = str
MetadataDict = Dict[str, Any]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a UNIX epoch float.

    Used consistently throughout the module so that all timestamps share
    the same reference frame and can be compared directly.  This shadows
    the identically-named helper in ``models.py`` so that the manifest
    module can be used in isolation.

    Returns
    -------
    float
        Seconds since the UNIX epoch, measured in UTC.
    """
    return time.time()


def _uid() -> str:
    """Generate a new random UUID4 string.

    All entity identifiers in this module are produced by this function,
    ensuring global uniqueness without coordination.

    Returns
    -------
    str
        A lower-case hyphenated UUID4 string.
    """
    return str(uuid.uuid4())


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp *v* to the closed interval [lo, hi].

    Parameters
    ----------
    v:
        Value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        ``max(lo, min(v, hi))``.
    """
    return max(lo, min(float(v), hi))


def _make_manifest_id() -> str:
    """Generate a unique manifest identifier with a human-readable prefix.

    The prefix ``mfst-`` makes log messages and debug output immediately
    identifiable as manifest-related.

    Returns
    -------
    str
        A string of the form ``"mfst-<uuid4>"``.
    """
    return f"mfst-{_uid()}"


def _make_ref_id() -> str:
    """Generate a unique evidence-reference identifier with a human-readable prefix.

    Returns
    -------
    str
        A string of the form ``"ref-<uuid4>"``.
    """
    return f"ref-{_uid()}"


def _normalise_trust(trust: float) -> float:
    """Normalise a trust value, clamping it to [0, 1] and rounding to 6 d.p.

    Parameters
    ----------
    trust:
        Raw trust value, expected in [0, 1] but clamped if outside.

    Returns
    -------
    float
        Normalised trust in [0.0, 1.0].
    """
    return round(_clamp(trust, 0.0, 1.0), 6)


def _evidence_coverage_factor(ref_count: int) -> float:
    """Map an evidence-reference count to a coverage factor in [0, 1].

    Uses a saturating function: each additional reference contributes
    diminishing returns.  The function reaches 0.5 at ``MIN_EVIDENCE_REFS_FOR_COVERAGE``
    references and asymptotically approaches 1.0.

    Parameters
    ----------
    ref_count:
        Number of evidence references in the manifest.

    Returns
    -------
    float
        Coverage factor in [0, 1].
    """
    if ref_count <= 0:
        return 0.0
    # tanh-based saturation
    k = ref_count / max(1, MIN_EVIDENCE_REFS_FOR_COVERAGE)
    return float(math.tanh(k))


def _resolution_rate(obstruction_fields: List[Any]) -> float:
    """Compute the fraction of obstructions that are non-blocking.

    A higher resolution rate means more obstructions have been resolved
    (severity < threshold).

    Parameters
    ----------
    obstruction_fields:
        List of ObstructionField instances (or duck-typed equivalents with
        an ``is_blocking()`` method).

    Returns
    -------
    float
        Fraction of non-blocking obstructions, or 1.0 if the list is empty.
    """
    if not obstruction_fields:
        return 1.0  # No obstructions → perfectly resolved
    non_blocking = sum(
        1 for obs in obstruction_fields
        if hasattr(obs, "is_blocking") and not obs.is_blocking()
    )
    return non_blocking / len(obstruction_fields)


# ---------------------------------------------------------------------------
# ManifestValidationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ManifestValidationResult:
    """An immutable record of the outcome of validating a manifest.

    ManifestValidationResult is produced by ``_validate_manifest_fields()``
    and carries the full set of errors and warnings discovered during
    validation, plus a normalised quality score.

    A manifest is considered *valid* if and only if ``is_valid`` is True.
    Warnings do not affect validity but should be reviewed before the
    manifest is used in production.

    The ``score`` field in [0, 1] quantifies manifest quality beyond the
    binary valid/invalid judgment.  It accounts for trust level, evidence
    coverage, obstruction resolution rate, and completeness of optional
    fields.  A score of 1.0 indicates a perfect manifest; 0.0 indicates
    a completely broken one.

    Attributes
    ----------
    is_valid : bool
        True if the manifest passed all required validation checks.
    errors : tuple[str, ...]
        Tuple of error messages.  Non-empty implies ``is_valid == False``.
    warnings : tuple[str, ...]
        Tuple of advisory warning messages.
    score : float
        Normalised quality score in [0, 1].

    See Also
    --------
    _validate_manifest_fields     : Produces ManifestValidationResult instances.
    RegimeBootstrappingManifest.validate : Calls _validate_manifest_fields.
    """

    is_valid: bool = True
    errors: Tuple[str, ...] = field(default_factory=tuple)
    warnings: Tuple[str, ...] = field(default_factory=tuple)
    score: float = 1.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def raise_if_invalid(self) -> None:
        """Raise a ValueError if this validation result contains errors.

        This method allows callers to use validation in an assertion style:

          >>> vr = manifest.validate()
          >>> vr.raise_if_invalid()  # raises only if errors exist

        Raises
        ------
        ValueError
            If ``self.is_valid`` is False.  The message includes all
            error strings joined by semicolons.
        """
        if not self.is_valid:
            msg = "ManifestValidationResult: " + "; ".join(self.errors)
            raise ValueError(msg)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this validation result to a plain Python dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation with all fields.
        """
        return {
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "score": self.score,
            "schema_version": MANIFEST_SCHEMA_VERSION,
        }

    def describe(self) -> str:
        """Return a human-readable multi-line description.

        Returns
        -------
        str
            Multi-line report including validity, score, errors, and warnings.
        """
        lines = [
            f"ManifestValidationResult",
            f"  valid  : {self.is_valid}",
            f"  score  : {self.score:.4f}",
            f"  errors : {len(self.errors)}",
            f"  warns  : {len(self.warnings)}",
        ]
        for e in self.errors:
            lines.append(f"    [ERR]  {e}")
        for w in self.warnings:
            lines.append(f"    [WARN] {w}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _has_error_containing(self, substring: str) -> bool:
        """Return True if any error message contains ``substring``.

        Parameters
        ----------
        substring:
            Substring to search for (case-sensitive).

        Returns
        -------
        bool
            True if at least one error contains the substring.
        """
        return any(substring in e for e in self.errors)


# ---------------------------------------------------------------------------
# _validate_manifest_fields
# ---------------------------------------------------------------------------


def _validate_manifest_fields(manifest: "RegimeBootstrappingManifest") -> ManifestValidationResult:
    """Validate all fields of a RegimeBootstrappingManifest and return a result.

    This function implements the validation logic described in Ch55 §7.
    It inspects each field of the manifest and accumulates errors and
    warnings, then computes a normalised quality score.

    Parameters
    ----------
    manifest:
        The manifest to validate.

    Returns
    -------
    ManifestValidationResult
        Validation outcome with errors, warnings, and score.

    Notes
    -----
    The function does not raise exceptions; call
    ``ManifestValidationResult.raise_if_invalid()`` to convert errors into
    an exception if desired.
    """
    errors: List[str] = []
    warnings: List[str] = []

    # --- manifest_id -------------------------------------------------------
    if not manifest.manifest_id:
        errors.append("manifest_id is empty or None")

    # --- plan_id -----------------------------------------------------------
    if not manifest.plan_id:
        errors.append("plan_id is required but is empty or None")

    # --- domain_formation --------------------------------------------------
    if manifest.domain_formation is None:
        errors.append("domain_formation is required but is None")
    else:
        try:
            if not manifest.domain_formation.is_valid():
                errors.append("domain_formation.is_valid() returned False")
        except Exception as exc:
            warnings.append(f"Could not check domain_formation validity: {exc}")

    # --- type_constructors -------------------------------------------------
    if not manifest.type_constructors:
        warnings.append("type_constructors list is empty — no constructors applied")
    else:
        for i, ctor in enumerate(manifest.type_constructors):
            try:
                if not ctor.validate_arity():
                    errors.append(
                        f"type_constructors[{i}] ({ctor.constructor_id}) has invalid arity"
                    )
            except Exception as exc:
                warnings.append(f"Could not validate type_constructors[{i}] arity: {exc}")

    # --- obstruction_fields ------------------------------------------------
    blocking = [
        obs for obs in manifest.obstruction_fields
        if hasattr(obs, "is_blocking") and obs.is_blocking()
    ]
    if blocking:
        errors.append(
            f"{len(blocking)} blocking obstruction field(s) present in manifest"
        )

    # --- trust_level -------------------------------------------------------
    if manifest.trust_level < TRUST_LEVEL_ERROR_THRESHOLD:
        errors.append(
            f"trust_level {manifest.trust_level:.4f} is below minimum "
            f"{TRUST_LEVEL_ERROR_THRESHOLD}"
        )
    elif manifest.trust_level < TRUST_LEVEL_WARNING_THRESHOLD:
        warnings.append(
            f"trust_level {manifest.trust_level:.4f} is low "
            f"(< {TRUST_LEVEL_WARNING_THRESHOLD})"
        )

    # --- evidence_refs -----------------------------------------------------
    if len(manifest.evidence_refs) < MIN_EVIDENCE_REFS_FOR_COVERAGE:
        warnings.append(
            f"only {len(manifest.evidence_refs)} evidence reference(s); "
            f"recommend >= {MIN_EVIDENCE_REFS_FOR_COVERAGE} for good coverage"
        )

    # --- created_at --------------------------------------------------------
    if manifest.created_at <= 0.0:
        errors.append("created_at must be a positive UNIX epoch timestamp")

    # --- finalized_at consistency ------------------------------------------
    if manifest.finalized_at is not None:
        if manifest.finalized_at < manifest.created_at:
            errors.append(
                "finalized_at is earlier than created_at — timeline inconsistency"
            )

    # --- score computation -------------------------------------------------
    # Deduct from 1.0 for each error (0.15) and warning (0.05), then apply
    # the coverage score as a multiplier so that poor coverage drags down
    # even otherwise-valid manifests.
    error_penalty = 0.15 * len(errors)
    warning_penalty = 0.05 * len(warnings)
    base_score = _clamp(1.0 - error_penalty - warning_penalty, 0.0, 1.0)
    # Multiply by coverage_score of the manifest for the final score
    try:
        coverage = manifest.coverage_score()
    except Exception:
        coverage = 0.5  # neutral fallback
    final_score = _clamp(base_score * coverage, 0.0, 1.0)

    return ManifestValidationResult(
        is_valid=len(errors) == 0,
        errors=tuple(errors),
        warnings=tuple(warnings),
        score=final_score,
    )


# ---------------------------------------------------------------------------
# RegimeBootstrappingManifest
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RegimeBootstrappingManifest:
    """The authoritative record of a regime-bootstrapping run.

    A RegimeBootstrappingManifest binds together all components produced by
    the bootstrapping pipeline — domain formation, type constructors,
    obstruction fields, evidence references — into a single versioned
    document that certifies the production of a regime.

    While ``finalized_at is None`` the manifest is *live* and can be
    modified (evidence refs added, trust level updated, etc.).  Once
    ``finalize()`` is called the ``finalized_at`` timestamp is recorded
    and subsequent modification calls raise ``RuntimeError``.

    Downstream consumers (e.g. the orchestrator, the evidence manager) can
    call ``validate()`` at any time to obtain a ``ManifestValidationResult``
    describing the manifest's current state.

    Attributes
    ----------
    manifest_id : str
        Unique manifest identifier, e.g. ``"mfst-3d9f1c2a-..."``.
    plan_id : str
        Identifier of the BootstrapPlan whose execution this manifest records.
    domain_formation : DomainFormation or None
        The domain formation at the heart of this manifest.
    type_constructors : list[TypeConstructor]
        Ordered list of type constructors applied during bootstrapping.
    obstruction_fields : list[ObstructionField]
        All obstruction fields encountered and their resolution status.
    evidence_refs : list[str]
        URI-like strings referencing external evidence documents.
    trust_level : float
        Normalised trust in [0, 1]; the aggregate confidence level.
    created_at : float
        UTC epoch timestamp of manifest creation.
    finalized_at : float or None
        UTC epoch timestamp when the manifest was finalized, or None.
    metadata : dict
        Arbitrary additional data.

    See Also
    --------
    BootstrappingManifestBuilder  : Preferred construction method.
    ManifestValidationResult      : Outcome of validate().
    _validate_manifest_fields     : Internal validation logic.
    """

    manifest_id: str = field(default_factory=_make_manifest_id)
    plan_id: str = ""
    domain_formation: Optional[Any] = None   # DomainFormation when models available
    type_constructors: List[Any] = field(default_factory=list)
    obstruction_fields: List[Any] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    trust_level: float = 0.5
    created_at: float = field(default_factory=_utcnow)
    finalized_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def add_evidence_ref(self, ref: str) -> None:
        """Append a new evidence reference URI to this manifest.

        Evidence references are URI-like strings that point to external
        documents, proofs, or records that support the manifest's claims.
        Duplicate references are silently ignored.

        Parameters
        ----------
        ref:
            URI or identifier of the evidence document, e.g.
            ``"ev://jugeo/evidence/0123"`` or ``"doi:10.1234/abc"``.

        Raises
        ------
        RuntimeError
            If the manifest has already been finalized.
        """
        self._assert_not_finalized("add_evidence_ref")
        if ref and ref not in self.evidence_refs:
            self.evidence_refs.append(ref)
            logger.debug("Manifest %s: added evidence ref %r", self.manifest_id, ref)

    def finalize(self) -> None:
        """Freeze this manifest by recording the finalization timestamp.

        After finalization, any attempt to mutate the manifest via its
        public API will raise ``RuntimeError``.  The manifest can still
        be read, validated, and serialised.

        Raises
        ------
        RuntimeError
            If the manifest has already been finalized (idempotent call
            raises to prevent accidental double-finalization).
        """
        if self.finalized_at is not None:
            raise RuntimeError(
                f"Manifest {self.manifest_id} is already finalized "
                f"(finalized_at={self.finalized_at:.3f})"
            )
        self.finalized_at = _utcnow()
        logger.info(
            "Manifest %s finalized at %.3f (trust=%.4f, refs=%d)",
            self.manifest_id, self.finalized_at, self.trust_level, len(self.evidence_refs),
        )

    def is_finalized(self) -> bool:
        """Return True if this manifest has been finalized.

        Returns
        -------
        bool
            True if ``self.finalized_at is not None``.
        """
        return self.finalized_at is not None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this manifest to a plain Python dictionary.

        All nested objects are recursively serialised via their own
        ``to_dict()`` methods if available, falling back to ``str()``
        for unknown types.

        Returns
        -------
        dict
            JSON-serialisable representation of the full manifest.
        """

        def _safe_to_dict(obj: Any) -> Any:
            if obj is None:
                return None
            if hasattr(obj, "to_dict"):
                return obj.to_dict()
            return str(obj)

        return {
            "manifest_id": self.manifest_id,
            "plan_id": self.plan_id,
            "domain_formation": _safe_to_dict(self.domain_formation),
            "type_constructors": [_safe_to_dict(c) for c in self.type_constructors],
            "obstruction_fields": [_safe_to_dict(o) for o in self.obstruction_fields],
            "evidence_refs": list(self.evidence_refs),
            "trust_level": self.trust_level,
            "created_at": self.created_at,
            "finalized_at": self.finalized_at,
            "metadata": dict(self.metadata),
            "is_finalized": self.is_finalized(),
            "schema_version": MANIFEST_SCHEMA_VERSION,
        }

    def render_summary(self) -> str:
        """Return a multi-line human-readable summary of this manifest.

        The summary is designed for logging and monitoring dashboards.
        It includes all key scalar fields and counts for list fields.

        Returns
        -------
        str
            Multi-line summary string.
        """
        status = "FINALIZED" if self.is_finalized() else "LIVE"
        fin_str = f"{self.finalized_at:.3f}" if self.finalized_at else "—"
        dom_id = (
            getattr(self.domain_formation, "domain_id", "(none)")
            if self.domain_formation else "(none)"
        )
        lines = [
            f"RegimeBootstrappingManifest [{status}]",
            f"  manifest_id      : {self.manifest_id}",
            f"  plan_id          : {self.plan_id}",
            f"  domain_formation : {dom_id}",
            f"  type_constructors: {len(self.type_constructors)}",
            f"  obstruction_flds : {len(self.obstruction_fields)}",
            f"  evidence_refs    : {len(self.evidence_refs)}",
            f"  trust_level      : {self.trust_level:.4f}",
            f"  coverage_score   : {self.coverage_score():.4f}",
            f"  created_at       : {self.created_at:.3f}",
            f"  finalized_at     : {fin_str}",
        ]
        return "\n".join(lines)

    def evidence_count(self) -> int:
        """Return the number of evidence references in this manifest.

        Returns
        -------
        int
            ``len(self.evidence_refs)``.
        """
        return len(self.evidence_refs)

    def coverage_score(self) -> float:
        """Compute a normalised coverage score for this manifest.

        The coverage score estimates how well-evidenced and well-resolved
        the manifest is, combining three factors:

          * evidence_factor : saturation of evidence-reference count
          * trust_factor    : normalised trust level
          * resolution_rate : fraction of non-blocking obstructions

        Formula::

            score = (COVERAGE_WEIGHT_EVIDENCE * evidence_factor
                   + COVERAGE_WEIGHT_TRUST * trust_level
                   + COVERAGE_WEIGHT_RESOLUTION * resolution_rate)

        Returns
        -------
        float
            Coverage score in [0, 1].
        """
        evidence_factor = _evidence_coverage_factor(len(self.evidence_refs))
        trust_factor = _normalise_trust(self.trust_level)
        resolution = _resolution_rate(self.obstruction_fields)

        score = (
            COVERAGE_WEIGHT_EVIDENCE * evidence_factor
            + COVERAGE_WEIGHT_TRUST * trust_factor
            + COVERAGE_WEIGHT_RESOLUTION * resolution
        )
        return _clamp(score, 0.0, 1.0)

    def validate(self) -> ManifestValidationResult:
        """Validate this manifest and return a ManifestValidationResult.

        Delegates to the module-level ``_validate_manifest_fields()``
        function so that validation logic can be tested independently of
        the manifest class.

        Returns
        -------
        ManifestValidationResult
            Result with ``is_valid``, ``errors``, ``warnings``, and ``score``.
        """
        return _validate_manifest_fields(self)

    def merge_with(self, other: "RegimeBootstrappingManifest") -> "RegimeBootstrappingManifest":
        """Return a new, unfinalized manifest merging self and other.

        The merged manifest:
          * inherits ``plan_id`` from ``self`` (takes precedence)
          * retains ``domain_formation`` from ``self``
          * unions type_constructors (deduped by constructor_id)
          * unions obstruction_fields (deduped by id)
          * unions evidence_refs (deduped)
          * takes ``max(self.trust_level, other.trust_level)``
          * receives a fresh manifest_id and created_at timestamp

        Finalized manifests can be merged, but the result is always
        unfinalized.

        Parameters
        ----------
        other:
            The manifest to merge with.

        Returns
        -------
        RegimeBootstrappingManifest
            New unfinalized merged manifest.
        """
        # Merge type constructors by constructor_id
        seen_ctor_ids = set()
        merged_ctors = []
        for ctor in self.type_constructors + other.type_constructors:
            cid = getattr(ctor, "constructor_id", id(ctor))
            if cid not in seen_ctor_ids:
                seen_ctor_ids.add(cid)
                merged_ctors.append(ctor)

        # Merge obstruction fields by id
        seen_obs_ids = set()
        merged_obs = []
        for obs in self.obstruction_fields + other.obstruction_fields:
            oid = getattr(obs, "id", id(obs))
            if oid not in seen_obs_ids:
                seen_obs_ids.add(oid)
                merged_obs.append(obs)

        # Merge evidence refs (dedup preserving order)
        merged_refs = list(dict.fromkeys(self.evidence_refs + other.evidence_refs))

        # Merge metadata (self takes precedence)
        merged_meta = {**other.metadata, **self.metadata}

        result = RegimeBootstrappingManifest(
            plan_id=self.plan_id or other.plan_id,
            domain_formation=self.domain_formation or other.domain_formation,
            type_constructors=merged_ctors,
            obstruction_fields=merged_obs,
            evidence_refs=merged_refs,
            trust_level=max(self.trust_level, other.trust_level),
            metadata=merged_meta,
        )
        logger.debug(
            "Merged manifests %s + %s → %s",
            self.manifest_id, other.manifest_id, result.manifest_id,
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _assert_not_finalized(self, caller: str) -> None:
        """Raise RuntimeError if the manifest is already finalized.

        Parameters
        ----------
        caller:
            Name of the calling method (for the error message).

        Raises
        ------
        RuntimeError
            If ``self.finalized_at is not None``.
        """
        if self.finalized_at is not None:
            raise RuntimeError(
                f"RegimeBootstrappingManifest.{caller}(): "
                f"manifest {self.manifest_id} is already finalized "
                f"(finalized_at={self.finalized_at:.3f})"
            )

    def _blocking_obstruction_ids(self) -> List[str]:
        """Return the IDs of all blocking obstruction fields.

        Returns
        -------
        list[str]
            IDs of obstructions with ``is_blocking() == True``.
        """
        return [
            getattr(obs, "id", str(id(obs)))
            for obs in self.obstruction_fields
            if hasattr(obs, "is_blocking") and obs.is_blocking()
        ]

    def _constructor_kind_counts(self) -> Dict[str, int]:
        """Return a dict mapping constructor kind → count.

        Returns
        -------
        dict[str, int]
            E.g. ``{"inductive": 3, "quotient": 1}``.
        """
        counts: Dict[str, int] = {}
        for ctor in self.type_constructors:
            kind = getattr(getattr(ctor, "kind", None), "value", "unknown")
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    def _elapsed_secs(self) -> Optional[float]:
        """Return the duration between creation and finalization in seconds.

        Returns
        -------
        float or None
            Duration in seconds, or None if not yet finalized.
        """
        if self.finalized_at is None:
            return None
        return self.finalized_at - self.created_at


# ---------------------------------------------------------------------------
# BootstrappingManifestBuilder
# ---------------------------------------------------------------------------


class BootstrappingManifestBuilder:
    """Builder for constructing RegimeBootstrappingManifest instances.

    The builder pattern separates the incremental assembly of a manifest
    from the validation and construction of the final object.  Each
    ``with_*`` and ``add_*`` method returns ``self`` to support method
    chaining.  The ``build()`` method performs full validation and raises
    ``ValueError`` if any required fields are missing or inconsistent.

    This class is the *preferred* way to create manifests from external
    code.  Direct dataclass construction is acceptable for tests and
    internal tooling.

    Example
    -------
    ::

        manifest = (
            BootstrappingManifestBuilder()
            .with_plan_id("plan-abc-123")
            .with_domain_formation(my_domain)
            .with_type_constructors([ctor_a, ctor_b])
            .add_obstruction_field(obs_x)
            .add_evidence_ref("ev://jugeo/evidence/0042")
            .set_trust_level(0.87)
            .build()
        )

    See Also
    --------
    RegimeBootstrappingManifest   : The object produced by build().
    build_bootstrapping_manifest  : Functional wrapper around this builder.
    """

    def __init__(self) -> None:
        """Initialise an empty builder with all fields set to their defaults.

        No validation is performed at construction time; validation is
        deferred to ``build()``.
        """
        self._plan_id: str = ""
        self._domain_formation: Optional[Any] = None
        self._type_constructors: List[Any] = []
        self._obstruction_fields: List[Any] = []
        self._evidence_refs: List[str] = []
        self._trust_level: float = 0.5
        self._metadata: Dict[str, Any] = {}
        # Track which fields have been explicitly set
        self._fields_set: set = set()
        logger.debug("BootstrappingManifestBuilder: initialised.")

    # ------------------------------------------------------------------
    # Builder methods (return self for chaining)
    # ------------------------------------------------------------------

    def with_plan_id(self, plan_id: str) -> "BootstrappingManifestBuilder":
        """Set the plan ID for the manifest being built.

        The plan_id is the only required field in a manifest — without it
        the manifest cannot be linked back to the plan that produced it.

        Parameters
        ----------
        plan_id:
            The identifier of the bootstrapping plan, e.g.
            ``"plan-3d9f1c2a-..."``.

        Returns
        -------
        BootstrappingManifestBuilder
            ``self`` for chaining.

        Raises
        ------
        ValueError
            If ``plan_id`` is empty or None.
        """
        if not plan_id:
            raise ValueError("plan_id must be a non-empty string")
        self._plan_id = plan_id
        self._fields_set.add("plan_id")
        return self

    def with_domain_formation(self, domain: Any) -> "BootstrappingManifestBuilder":
        """Set the domain formation for the manifest being built.

        The domain formation is the core mathematical object that the
        bootstrapping pipeline operated on.

        Parameters
        ----------
        domain:
            A DomainFormation instance (or duck-typed equivalent).

        Returns
        -------
        BootstrappingManifestBuilder
            ``self`` for chaining.
        """
        self._domain_formation = domain
        self._fields_set.add("domain_formation")
        return self

    def with_type_constructors(self, ctors: List[Any]) -> "BootstrappingManifestBuilder":
        """Set the complete list of type constructors for the manifest.

        This replaces any previously set constructors.  Use
        ``with_type_constructors([])`` to clear the list.

        Parameters
        ----------
        ctors:
            List of TypeConstructor instances.

        Returns
        -------
        BootstrappingManifestBuilder
            ``self`` for chaining.
        """
        self._type_constructors = list(ctors)
        self._fields_set.add("type_constructors")
        return self

    def add_obstruction_field(self, obs: Any) -> "BootstrappingManifestBuilder":
        """Append a single obstruction field to the manifest.

        Parameters
        ----------
        obs:
            An ObstructionField instance to add.

        Returns
        -------
        BootstrappingManifestBuilder
            ``self`` for chaining.
        """
        self._obstruction_fields.append(obs)
        return self

    def add_evidence_ref(self, ref: str) -> "BootstrappingManifestBuilder":
        """Append a single evidence reference URI to the manifest.

        Duplicate references are silently ignored.

        Parameters
        ----------
        ref:
            URI or identifier string for the evidence document.

        Returns
        -------
        BootstrappingManifestBuilder
            ``self`` for chaining.
        """
        if ref and ref not in self._evidence_refs:
            self._evidence_refs.append(ref)
        return self

    def set_trust_level(self, trust: float) -> "BootstrappingManifestBuilder":
        """Set the trust level for the manifest being built.

        The trust level is clamped to [0, 1] before being stored.

        Parameters
        ----------
        trust:
            Proposed trust level in [0, 1].

        Returns
        -------
        BootstrappingManifestBuilder
            ``self`` for chaining.
        """
        self._trust_level = _normalise_trust(trust)
        self._fields_set.add("trust_level")
        return self

    def set_metadata(self, metadata: Dict[str, Any]) -> "BootstrappingManifestBuilder":
        """Set the metadata dict for the manifest being built.

        Parameters
        ----------
        metadata:
            Arbitrary dict to attach to the manifest.

        Returns
        -------
        BootstrappingManifestBuilder
            ``self`` for chaining.
        """
        self._metadata = dict(metadata)
        return self

    def build(self) -> RegimeBootstrappingManifest:
        """Construct and return the RegimeBootstrappingManifest.

        Performs pre-construction validation:
          1. ``plan_id`` must have been set via ``with_plan_id()``.
          2. ``domain_formation`` must be provided.
          3. All provided type constructors must pass ``validate_arity()``.

        Any blocking obstruction fields will NOT prevent the manifest from
        being built — they are recorded as-is, and the caller can decide
        whether to finalize or retry.

        Returns
        -------
        RegimeBootstrappingManifest
            Newly constructed, unfinalized manifest.

        Raises
        ------
        ValueError
            If required fields are absent or invalid.
        """
        # --- required field checks -----------------------------------------
        if not self._plan_id:
            raise ValueError(
                "BootstrappingManifestBuilder.build(): plan_id is required. "
                "Call with_plan_id() before build()."
            )
        if self._domain_formation is None:
            raise ValueError(
                "BootstrappingManifestBuilder.build(): domain_formation is required. "
                "Call with_domain_formation() before build()."
            )

        # --- type constructor arity validation -----------------------------
        for i, ctor in enumerate(self._type_constructors):
            try:
                if not ctor.validate_arity():
                    raise ValueError(
                        f"BootstrappingManifestBuilder.build(): "
                        f"type_constructors[{i}] has invalid arity "
                        f"(constructor_id={getattr(ctor, 'constructor_id', '?')})"
                    )
            except AttributeError:
                pass  # Duck-typed ctor without validate_arity — skip

        # --- construct manifest --------------------------------------------
        manifest = RegimeBootstrappingManifest(
            plan_id=self._plan_id,
            domain_formation=self._domain_formation,
            type_constructors=list(self._type_constructors),
            obstruction_fields=list(self._obstruction_fields),
            evidence_refs=list(self._evidence_refs),
            trust_level=self._trust_level,
            metadata=dict(self._metadata),
        )
        logger.info(
            "BootstrappingManifestBuilder.build(): created manifest %s "
            "(plan=%s trust=%.4f refs=%d ctors=%d obs=%d)",
            manifest.manifest_id,
            manifest.plan_id,
            manifest.trust_level,
            len(manifest.evidence_refs),
            len(manifest.type_constructors),
            len(manifest.obstruction_fields),
        )
        return manifest

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _describe_state(self) -> str:
        """Return a diagnostic string describing the builder's current state.

        Useful for debugging during complex pipelines.

        Returns
        -------
        str
            Multi-line description of builder state.
        """
        lines = [
            "BootstrappingManifestBuilder state:",
            f"  plan_id           : {self._plan_id or '(not set)'}",
            f"  domain_formation  : {self._domain_formation is not None}",
            f"  type_constructors : {len(self._type_constructors)}",
            f"  obstruction_fields: {len(self._obstruction_fields)}",
            f"  evidence_refs     : {len(self._evidence_refs)}",
            f"  trust_level       : {self._trust_level:.4f}",
            f"  fields_set        : {sorted(self._fields_set)}",
        ]
        return "\n".join(lines)

    def _reset(self) -> None:
        """Reset all builder fields to their initial defaults.

        This method is provided for reuse of a builder instance when
        constructing multiple manifests in a loop.
        """
        self.__init__()


# ---------------------------------------------------------------------------
# Free function: build_bootstrapping_manifest
# ---------------------------------------------------------------------------


def build_bootstrapping_manifest(
    plan_id: str,
    domain_formation: Any,
    type_constructors: Optional[List[Any]] = None,
    obstruction_fields: Optional[List[Any]] = None,
    evidence_refs: Optional[List[str]] = None,
    trust_level: float = 0.5,
    metadata: Optional[Dict[str, Any]] = None,
    auto_finalize: bool = False,
) -> RegimeBootstrappingManifest:
    """Construct a RegimeBootstrappingManifest using the builder, then return it.

    This is a convenience function that wraps ``BootstrappingManifestBuilder``
    in a single call.  It is suitable for callers that have all manifest data
    available upfront and prefer a functional style.

    Parameters
    ----------
    plan_id:
        The identifier of the bootstrapping plan.  Required.
    domain_formation:
        The domain formation at the heart of the manifest.  Required.
    type_constructors:
        List of TypeConstructor instances.  Defaults to ``[]``.
    obstruction_fields:
        List of ObstructionField instances.  Defaults to ``[]``.
    evidence_refs:
        List of evidence reference URI strings.  Defaults to ``[]``.
    trust_level:
        Aggregate trust level in [0, 1].  Defaults to 0.5.
    metadata:
        Arbitrary metadata dict.  Defaults to ``{}``.
    auto_finalize:
        If True, call ``manifest.finalize()`` before returning.

    Returns
    -------
    RegimeBootstrappingManifest
        The constructed manifest (finalized if ``auto_finalize`` is True).

    Raises
    ------
    ValueError
        If ``plan_id`` or ``domain_formation`` are invalid.

    Examples
    --------
    ::

        manifest = build_bootstrapping_manifest(
            plan_id="plan-abc",
            domain_formation=my_domain,
            evidence_refs=["ev://ref/001"],
            trust_level=0.9,
            auto_finalize=True,
        )
        assert manifest.is_finalized()
    """
    builder = BootstrappingManifestBuilder()
    builder.with_plan_id(plan_id)
    builder.with_domain_formation(domain_formation)
    builder.with_type_constructors(type_constructors or [])
    builder.set_trust_level(trust_level)
    if metadata:
        builder.set_metadata(metadata)
    for obs in (obstruction_fields or []):
        builder.add_obstruction_field(obs)
    for ref in (evidence_refs or []):
        builder.add_evidence_ref(ref)

    manifest = builder.build()

    if auto_finalize:
        manifest.finalize()

    return manifest
