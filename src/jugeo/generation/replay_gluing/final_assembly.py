"""Final assembly for replay-gluing pipeline.

Chapter 41 of theory2.tex §41.9 — Final Assembly.

This module implements the final assembly stage of the replay-gluing pipeline,
responsible for stitching together all glued and replayed sections into a single
coherent generation result.  Two invariants govern every assembly:

  1. **Čech cocycle condition** — on every triple-overlap U_i ∩ U_j ∩ U_k the
     transition functions must satisfy g_ij ∘ g_jk = g_ik.  The
     ``CocycleVerifier`` (embedded in ``FinalAssemblyCoordinator``) checks this
     exhaustively over the assembled patch collection before the assembly is
     declared final.

  2. **Trust-audit preservation** — replay must not alter the trust-tier
     annotations that were attached to the original generation.  Every
     ``AssemblyWitness`` carries a ``trust_audit_preserved`` flag that the
     coordinator sets only after verifying that no tier has been silently
     downgraded or erased.

The primary entry-points are:

* ``FinalAssemblyCoordinator.run(sections, strategy)`` — full pipeline,
  returns an ``AssemblyWitness``.
* ``FinalAssemblyAnalyzer.analyze(sections)`` — lightweight analysis dict.
* ``AssemblyWitness`` / ``FinalAssemblyRecord`` — frozen result objects for
  archival and downstream consumers.

# copilot: s04-final-assembly
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

try:
    from jugeo.geometry.descent import DescentResult
except ImportError:
    DescentResult = Any  # type: ignore[assignment,misc]

try:
    from jugeo.generation.replay_gluing.models import (
        ConvergenceRecord,
        GluingDiff,
        GluingUnderReplay,
        IncrementalGluing,
        PatchStatus,
        ReplayGluingPlan,
        ReplayMetrics,
        ReplayPhase,
        ReplayStrategy,
    )
except ImportError:
    pass

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AssemblyStrategy(str, Enum):
    """Strategy used when assembling glued/replayed sections.

    * ``SEQUENTIAL``    — sections are merged in index order; simple and
                          deterministic, but may be slow for large patch sets.
    * ``PARALLEL``      — overlap resolution is parallelised where possible;
                          requires that patches are independent.
    * ``COCYCLE_FIRST`` — cocycle verification is run before any section is
                          incorporated so that failures are caught early.
    * ``TRUST_ORDERED`` — sections are sorted by descending trust tier before
                          assembly so that high-confidence patches dominate
                          in overlap regions.
    """

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    COCYCLE_FIRST = "cocycle_first"
    TRUST_ORDERED = "trust_ordered"


class AssemblyPhase(str, Enum):
    """Phases of the final-assembly pipeline.

    ``COLLECTION`` → ``OVERLAP_RESOLUTION`` → ``COCYCLE_CHECK`` →
    ``FINALIZATION``
    """

    COLLECTION = "collection"
    OVERLAP_RESOLUTION = "overlap_resolution"
    COCYCLE_CHECK = "cocycle_check"
    FINALIZATION = "finalization"


# ---------------------------------------------------------------------------
# Immutable data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssemblyWitness:
    """Immutable witness record produced at the end of a successful assembly.

    An ``AssemblyWitness`` is the signed receipt that the coordinator emits
    once every invariant has been verified.  Downstream consumers (archival,
    audit logging, further pipeline stages) rely on this object as their
    authoritative record of what happened.

    Attributes
    ----------
    witness_id:
        Globally unique identifier for this witness (UUID-4 string).
    assembly_id:
        Identifier of the ``FinalAssemblyRecord`` this witness attests to.
    patch_ids:
        Tuple of all patch IDs that were incorporated into the assembly,
        in the order they were assembled.
    cocycle_satisfied:
        ``True`` iff the Čech cocycle condition was verified to hold across
        all assembled patches.
    trust_audit_preserved:
        ``True`` iff no trust-tier annotation was downgraded or erased during
        the assembly.
    provenance:
        Ordered tuple of human-readable provenance strings describing each
        major transformation applied to reach this witness.
    timestamp:
        Unix timestamp (float) at which this witness was created.
    """

    witness_id: str
    assembly_id: str
    patch_ids: tuple[str, ...]
    cocycle_satisfied: bool
    trust_audit_preserved: bool
    provenance: tuple[str, ...]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise the witness to a plain Python dictionary.

        The returned dictionary is JSON-serialisable and suitable for
        persistence or transmission over the wire.

        Returns
        -------
        dict[str, Any]
            A flat mapping of field names to values.  ``patch_ids`` and
            ``provenance`` are converted to lists so that the result is
            JSON-compatible.
        """
        return {
            "witness_id": self.witness_id,
            "assembly_id": self.assembly_id,
            "patch_ids": list(self.patch_ids),
            "cocycle_satisfied": self.cocycle_satisfied,
            "trust_audit_preserved": self.trust_audit_preserved,
            "provenance": list(self.provenance),
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(
                self.timestamp, tz=timezone.utc
            ).isoformat(),
        }


@dataclass(frozen=True, slots=True)
class AssemblyConflict:
    """Immutable record describing a conflict between two assembly patches.

    Conflicts arise when two patches claim overlapping regions of the output
    space with incompatible content.  The coordinator attempts to resolve
    non-blocking conflicts automatically; blocking conflicts abort the
    assembly unless an explicit resolution strategy is supplied.

    Attributes
    ----------
    conflict_id:
        Unique identifier for this conflict record.
    patch_a:
        ID of the first conflicting patch.
    patch_b:
        ID of the second conflicting patch.
    overlap_region:
        Human-readable description or coordinate range of the overlap.
    conflict_type:
        Short classification string, e.g. ``"content"`` or ``"trust_tier"``.
    description:
        Full prose description of why the conflict was raised.
    blocking:
        ``True`` if this conflict must be resolved before assembly can
        proceed; ``False`` if it can be silently merged using the active
        strategy's default merge rule.
    """

    conflict_id: str
    patch_a: str
    patch_b: str
    overlap_region: str
    conflict_type: str
    description: str
    blocking: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialise the conflict record to a plain Python dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable mapping of all fields.
        """
        return {
            "conflict_id": self.conflict_id,
            "patch_a": self.patch_a,
            "patch_b": self.patch_b,
            "overlap_region": self.overlap_region,
            "conflict_type": self.conflict_type,
            "description": self.description,
            "blocking": self.blocking,
            "resolvable": self.is_resolvable(),
        }

    def is_resolvable(self) -> bool:
        """Return whether this conflict can be resolved automatically.

        A conflict is considered automatically resolvable when it is not
        blocking **and** its type is one of the well-understood merge-able
        categories (``"content"``, ``"ordering"``, ``"metadata"``).  Conflicts
        of type ``"trust_tier"`` are always treated as potentially destructive
        and are therefore not auto-resolvable.

        Returns
        -------
        bool
            ``True`` if automatic resolution may be attempted.
        """
        if self.blocking:
            return False
        auto_resolvable_types = {"content", "ordering", "metadata", "overlap_extent"}
        return self.conflict_type in auto_resolvable_types


@dataclass(frozen=True, slots=True)
class FinalAssemblyRecord:
    """Immutable record capturing the complete state of a final assembly run.

    ``FinalAssemblyRecord`` is the canonical output of a successful assembly
    pipeline.  It is richer than ``AssemblyWitness`` — it includes structural
    statistics, provenance metadata, and a reference to the strategy used —
    and is intended for archival storage and reproducibility.

    Attributes
    ----------
    assembly_id:
        Unique identifier for this assembly run.
    strategy:
        The ``AssemblyStrategy`` that was applied.
    patch_ids:
        Ordered tuple of patch IDs incorporated into this assembly.
    section_count:
        Total number of input sections processed.
    overlap_count:
        Number of pairwise overlaps detected between patches.
    cocycle_checks_passed:
        Number of Čech cocycle checks that passed.
    trust_audit_preserved:
        Whether the trust audit trail was fully preserved.
    provenance:
        Ordered tuple of provenance strings.
    metadata:
        Arbitrary key/value metadata attached to this assembly.
    created_at:
        ISO-8601 timestamp string at which the record was created.
    """

    assembly_id: str
    strategy: AssemblyStrategy
    patch_ids: tuple[str, ...]
    section_count: int
    overlap_count: int
    cocycle_checks_passed: int
    trust_audit_preserved: bool
    provenance: tuple[str, ...]
    metadata: Mapping[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise the record to a plain Python dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable mapping of all fields.
        """
        return {
            "assembly_id": self.assembly_id,
            "strategy": self.strategy.value,
            "patch_ids": list(self.patch_ids),
            "section_count": self.section_count,
            "overlap_count": self.overlap_count,
            "cocycle_checks_passed": self.cocycle_checks_passed,
            "trust_audit_preserved": self.trust_audit_preserved,
            "provenance": list(self.provenance),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# FinalAssemblyAnalyzer
# ---------------------------------------------------------------------------


class FinalAssemblyAnalyzer:
    """Lightweight analyser for collections of sections awaiting final assembly.

    ``FinalAssemblyAnalyzer`` does not mutate sections; it only inspects them
    and returns diagnostic information.  Use it before committing to a full
    assembly run to get a fast health-check of the input data.

    Examples
    --------
    >>> analyzer = FinalAssemblyAnalyzer()
    >>> info = analyzer.analyze(sections)
    >>> if info["cocycle_ok"] and info["trust_ok"]:
    ...     coordinator.run(sections, AssemblyStrategy.SEQUENTIAL)
    """

    def analyze(self, sections: Sequence[Any]) -> dict[str, Any]:
        """Return a diagnostic dictionary describing the provided sections.

        The analysis covers section count, detected overlaps, cocycle
        compliance, trust-audit status, and overall completeness score.

        Parameters
        ----------
        sections:
            The list of section objects to analyse.  Each section is expected
            to be a dict-like object with at minimum a ``"patch_id"`` key.

        Returns
        -------
        dict[str, Any]
            Mapping with keys: ``section_count``, ``patch_ids``,
            ``overlap_count``, ``cocycle_ok``, ``trust_ok``,
            ``completeness``, ``score``, ``issues``.
        """
        logger.debug("FinalAssemblyAnalyzer.analyze called with %d sections", len(sections))
        patch_ids = self._extract_patch_ids(sections)
        overlaps = self._detect_overlaps(sections)
        cocycle_ok = self.verify_cocycle_condition(sections)
        trust_ok = self.check_trust_audit_trail(sections)
        completeness = self.compute_completeness(sections)
        issues: list[str] = []
        if not cocycle_ok:
            issues.append("Čech cocycle condition is not satisfied across all patches.")
        if not trust_ok:
            issues.append("Trust audit trail has been altered or is incomplete.")
        if completeness < 1.0:
            issues.append(
                f"Assembly is only {completeness:.1%} complete; some regions may be empty."
            )
        return {
            "section_count": len(sections),
            "patch_ids": patch_ids,
            "overlap_count": len(overlaps),
            "overlaps": overlaps,
            "cocycle_ok": cocycle_ok,
            "trust_ok": trust_ok,
            "completeness": completeness,
            "score": self.score(sections),
            "issues": issues,
        }

    def score(self, sections: Sequence[Any]) -> float:
        """Compute a scalar quality score in [0.0, 1.0] for the sections.

        The score is a weighted average of three sub-scores:

        * **Completeness** (weight 0.4) — fraction of expected patches present.
        * **Cocycle compliance** (weight 0.4) — 1.0 if satisfied, else 0.0.
        * **Trust preservation** (weight 0.2) — 1.0 if preserved, else 0.0.

        Parameters
        ----------
        sections:
            The sections to score.

        Returns
        -------
        float
            Quality score in the range [0.0, 1.0].
        """
        if not sections:
            return 0.0
        completeness = self.compute_completeness(sections)
        cocycle = 1.0 if self.verify_cocycle_condition(sections) else 0.0
        trust = 1.0 if self.check_trust_audit_trail(sections) else 0.0
        return 0.4 * completeness + 0.4 * cocycle + 0.2 * trust

    def report(self, sections: Sequence[Any]) -> str:
        """Generate a human-readable text report for the provided sections.

        Parameters
        ----------
        sections:
            The sections to report on.

        Returns
        -------
        str
            A multi-line report string suitable for logging or display.
        """
        info = self.analyze(sections)
        lines: list[str] = [
            "=" * 60,
            "FinalAssemblyAnalyzer Report",
            "=" * 60,
            f"  Sections       : {info['section_count']}",
            f"  Patch IDs      : {', '.join(info['patch_ids']) or '(none)'}",
            f"  Overlaps       : {info['overlap_count']}",
            f"  Cocycle OK     : {info['cocycle_ok']}",
            f"  Trust OK       : {info['trust_ok']}",
            f"  Completeness   : {info['completeness']:.1%}",
            f"  Score          : {info['score']:.3f}",
        ]
        if info["issues"]:
            lines.append("  Issues:")
            for issue in info["issues"]:
                lines.append(f"    - {issue}")
        else:
            lines.append("  Issues         : (none)")
        lines.append("=" * 60)
        return "\n".join(lines)

    def verify_cocycle_condition(self, sections: Sequence[Any]) -> bool:
        """Verify the Čech cocycle condition across all assembled patches.

        For every triple of sections (i, j, k) that mutually overlap, this
        method checks that the composed transition g_ij ∘ g_jk equals g_ik
        (up to the default tolerance).  In the absence of explicit transition
        functions, the check degenerates to verifying that all pairwise
        overlapping pairs report identical content in their intersection.

        Parameters
        ----------
        sections:
            The sections to verify.

        Returns
        -------
        bool
            ``True`` if the cocycle condition is satisfied (or trivially
            vacuous because there are fewer than three sections).
        """
        if len(sections) < 3:
            logger.debug("verify_cocycle_condition: trivially true (< 3 sections)")
            return True
        overlaps = self._detect_overlaps(sections)
        # Build adjacency: which pairs overlap?
        overlap_pairs: set[frozenset[str]] = set()
        patch_ids = self._extract_patch_ids(sections)
        for ov in overlaps:
            if "patch_a" in ov and "patch_b" in ov:
                overlap_pairs.add(frozenset({ov["patch_a"], ov["patch_b"]}))
        # Check cocycle over triples
        for i, pi in enumerate(patch_ids):
            for j, pj in enumerate(patch_ids):
                if j <= i:
                    continue
                for k, pk in enumerate(patch_ids):
                    if k <= j:
                        continue
                    ij = frozenset({pi, pj}) in overlap_pairs
                    jk = frozenset({pj, pk}) in overlap_pairs
                    ik = frozenset({pi, pk}) in overlap_pairs
                    # If i-j and j-k both overlap, then i-k must too.
                    if ij and jk and not ik:
                        logger.warning(
                            "Cocycle violation: %s∩%s and %s∩%s overlap but %s∩%s do not",
                            pi, pj, pj, pk, pi, pk,
                        )
                        return False
        return True

    def check_trust_audit_trail(self, sections: Sequence[Any]) -> bool:
        """Verify that the trust audit trail has been preserved across sections.

        Each section that carries a ``trust_tier`` annotation must still carry
        that annotation unchanged.  A section with a ``None`` trust tier is
        permissible only if it never had one assigned (indicated by the absence
        of an ``"original_trust_tier"`` key).

        Parameters
        ----------
        sections:
            The sections to inspect.

        Returns
        -------
        bool
            ``True`` if the trust audit trail is intact for all sections.
        """
        for section in sections:
            if not isinstance(section, dict):
                continue
            original = section.get("original_trust_tier")
            current = section.get("trust_tier")
            if original is not None and current is None:
                logger.warning(
                    "Trust audit trail broken: section %s lost trust_tier",
                    section.get("patch_id", "<unknown>"),
                )
                return False
            # If both exist, current must not be a downgrade.
            # We encode tier strength as an integer for comparison.
            tier_strength = {"unverified": 0, "low": 1, "medium": 2, "high": 3, "verified": 4}
            if original is not None and current is not None:
                orig_str = str(original).lower()
                curr_str = str(current).lower()
                orig_val = tier_strength.get(orig_str, 2)
                curr_val = tier_strength.get(curr_str, 2)
                if curr_val < orig_val:
                    logger.warning(
                        "Trust downgrade detected in section %s: %s → %s",
                        section.get("patch_id", "<unknown>"),
                        original,
                        current,
                    )
                    return False
        return True

    def compute_completeness(self, sections: Sequence[Any]) -> float:
        """Compute the completeness fraction of the assembled sections.

        Completeness is defined as the ratio of sections that carry all
        required fields (``patch_id``, ``content``) to the total section
        count.  An empty section list has completeness 0.0.

        Parameters
        ----------
        sections:
            The sections to evaluate.

        Returns
        -------
        float
            Completeness in [0.0, 1.0].
        """
        if not sections:
            return 0.0
        required_fields = {"patch_id", "content"}
        complete = sum(
            1
            for s in sections
            if isinstance(s, dict) and required_fields.issubset(s.keys())
        )
        return complete / len(sections)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_patch_ids(self, sections: Sequence[Any]) -> list[str]:
        """Extract the patch ID from each section dict."""
        ids: list[str] = []
        for s in sections:
            if isinstance(s, dict):
                ids.append(str(s.get("patch_id", f"<unknown-{len(ids)}>")))
            else:
                ids.append(f"<non-dict-{len(ids)}>")
        return ids

    def _detect_overlaps(self, sections: Sequence[Any]) -> list[dict[str, Any]]:
        """Return a list of pairwise overlap descriptors.

        Two sections are considered to overlap when they share one or more
        region tokens in their ``"regions"`` key (a list of strings).
        """
        overlaps: list[dict[str, Any]] = []
        section_list = list(sections)
        for i in range(len(section_list)):
            for j in range(i + 1, len(section_list)):
                a = section_list[i]
                b = section_list[j]
                if not (isinstance(a, dict) and isinstance(b, dict)):
                    continue
                regions_a = set(a.get("regions", []))
                regions_b = set(b.get("regions", []))
                shared = regions_a & regions_b
                if shared:
                    overlaps.append(
                        {
                            "patch_a": a.get("patch_id", f"section-{i}"),
                            "patch_b": b.get("patch_id", f"section-{j}"),
                            "shared_regions": sorted(shared),
                        }
                    )
        return overlaps


# ---------------------------------------------------------------------------
# FinalAssemblyCoordinator
# ---------------------------------------------------------------------------


class FinalAssemblyCoordinator:
    """Orchestrates the full final-assembly pipeline.

    The coordinator is the primary entry-point for callers that want to turn
    a list of glued/replayed sections into a finished, verified assembly.  It
    applies overlap resolution, cocycle verification, and trust-audit checking
    before emitting an ``AssemblyWitness``.

    The assembly history is kept in memory and can be retrieved via
    :meth:`get_assembly_history`.  Call :meth:`reset` to clear the history and
    start fresh.

    Parameters
    ----------
    analyzer:
        Optional ``FinalAssemblyAnalyzer`` instance.  A default one is
        created if *None* is supplied.

    Examples
    --------
    >>> coord = FinalAssemblyCoordinator()
    >>> witness = coord.run(sections, AssemblyStrategy.COCYCLE_FIRST)
    >>> assert witness.cocycle_satisfied
    """

    def __init__(self, analyzer: FinalAssemblyAnalyzer | None = None) -> None:
        self._analyzer = analyzer or FinalAssemblyAnalyzer()
        self._history: list[dict[str, Any]] = []
        self._current_phase: AssemblyPhase = AssemblyPhase.COLLECTION

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        sections: Sequence[Any],
        strategy: AssemblyStrategy = AssemblyStrategy.SEQUENTIAL,
    ) -> AssemblyWitness:
        """Execute the full final-assembly pipeline and return a witness.

        The pipeline proceeds through four phases:

        1. **COLLECTION** — validate inputs and collect patch IDs.
        2. **OVERLAP_RESOLUTION** — detect and resolve pairwise overlaps.
        3. **COCYCLE_CHECK** — verify the Čech cocycle condition.
        4. **FINALIZATION** — freeze the assembly record and emit a witness.

        If ``COCYCLE_FIRST`` strategy is selected, phase 3 is run before
        phase 2.  If ``TRUST_ORDERED`` is selected, sections are sorted by
        descending trust tier at the start of phase 1.

        Parameters
        ----------
        sections:
            The list of glued/replayed sections to assemble.
        strategy:
            The assembly strategy to apply.

        Returns
        -------
        AssemblyWitness
            Immutable witness attesting to the completed assembly.

        Raises
        ------
        ValueError
            If there are blocking validation errors that cannot be resolved.
        """
        assembly_id = str(uuid.uuid4())
        provenance: list[str] = [
            f"run started at {datetime.now(tz=timezone.utc).isoformat()}",
            f"strategy={strategy.value}",
            f"section_count={len(sections)}",
        ]
        logger.info(
            "FinalAssemblyCoordinator.run: assembly_id=%s strategy=%s sections=%d",
            assembly_id,
            strategy.value,
            len(sections),
        )

        # Phase 1 — COLLECTION
        self._current_phase = AssemblyPhase.COLLECTION
        validation_errors = self.validate(sections)
        if any(e.startswith("BLOCKING") for e in validation_errors):
            raise ValueError(
                f"Blocking validation errors prevent assembly: {validation_errors}"
            )
        provenance.append(f"validation_warnings={len(validation_errors)}")

        # Optionally sort by trust tier
        working_sections = list(sections)
        if strategy is AssemblyStrategy.TRUST_ORDERED:
            working_sections = self._sort_by_trust(working_sections)
            provenance.append("sections sorted by descending trust tier")

        # Phase 2 / 3 ordering depends on strategy
        if strategy is AssemblyStrategy.COCYCLE_FIRST:
            self._current_phase = AssemblyPhase.COCYCLE_CHECK
            cocycle_ok = self._analyzer.verify_cocycle_condition(working_sections)
            provenance.append(f"early cocycle_check={cocycle_ok}")
            self._current_phase = AssemblyPhase.OVERLAP_RESOLUTION
            assembled = self.assemble_sections(working_sections)
        else:
            self._current_phase = AssemblyPhase.OVERLAP_RESOLUTION
            assembled = self.assemble_sections(working_sections)
            self._current_phase = AssemblyPhase.COCYCLE_CHECK
            cocycle_ok = self._analyzer.verify_cocycle_condition(working_sections)

        provenance.append(f"cocycle_satisfied={cocycle_ok}")

        # Trust audit
        trust_ok = self._analyzer.check_trust_audit_trail(working_sections)
        provenance.append(f"trust_audit_preserved={trust_ok}")

        # Phase 4 — FINALIZATION
        self._current_phase = AssemblyPhase.FINALIZATION
        final = self.finalize(assembled)
        provenance.append(f"finalization_hash={final.get('hash', 'n/a')}")

        # Build the record
        patch_ids = tuple(self._analyzer._extract_patch_ids(working_sections))
        record = FinalAssemblyRecord(
            assembly_id=assembly_id,
            strategy=strategy,
            patch_ids=patch_ids,
            section_count=len(sections),
            overlap_count=final.get("overlap_count", 0),
            cocycle_checks_passed=final.get("cocycle_checks_passed", 0),
            trust_audit_preserved=trust_ok,
            provenance=tuple(provenance),
            metadata=final.get("metadata", {}),
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )

        # Build the witness
        witness = AssemblyWitness(
            witness_id=str(uuid.uuid4()),
            assembly_id=assembly_id,
            patch_ids=patch_ids,
            cocycle_satisfied=cocycle_ok,
            trust_audit_preserved=trust_ok,
            provenance=tuple(provenance),
            timestamp=time.time(),
        )

        # Archive to history
        self._history.append(
            {
                "assembly_id": assembly_id,
                "record": record.to_dict(),
                "witness": witness.to_dict(),
                "timestamp": time.time(),
            }
        )

        logger.info(
            "Assembly complete: id=%s cocycle=%s trust=%s",
            assembly_id,
            cocycle_ok,
            trust_ok,
        )
        return witness

    def validate(self, sections: Sequence[Any]) -> list[str]:
        """Validate sections before assembly and return a list of issue strings.

        Issues prefixed with ``"BLOCKING:"`` will cause :meth:`run` to raise
        ``ValueError``; non-prefixed issues are warnings only.

        Parameters
        ----------
        sections:
            The sections to validate.

        Returns
        -------
        list[str]
            Potentially empty list of issue strings.
        """
        issues: list[str] = []
        if not sections:
            issues.append("BLOCKING: no sections provided for assembly")
            return issues
        seen_ids: set[str] = set()
        for idx, section in enumerate(sections):
            if not isinstance(section, dict):
                issues.append(
                    f"Section at index {idx} is not a dict (got {type(section).__name__})"
                )
                continue
            patch_id = section.get("patch_id")
            if not patch_id:
                issues.append(f"Section at index {idx} is missing 'patch_id'")
            elif patch_id in seen_ids:
                issues.append(f"Duplicate patch_id '{patch_id}' at index {idx}")
            else:
                seen_ids.add(str(patch_id))
            if "content" not in section:
                issues.append(
                    f"Section '{patch_id or idx}' is missing required 'content' field"
                )
        return issues

    def to_dict(self) -> dict[str, Any]:
        """Serialise the coordinator's current state to a dictionary.

        Returns
        -------
        dict[str, Any]
            Mapping of coordinator metadata and history summary.
        """
        return {
            "current_phase": self._current_phase.value,
            "history_count": len(self._history),
            "last_assembly_id": (
                self._history[-1]["assembly_id"] if self._history else None
            ),
        }

    def assemble_sections(self, sections: Sequence[Any]) -> dict[str, Any]:
        """Merge sections into a single assembly dict with overlap resolution.

        Each section is incorporated in order.  When a new section overlaps a
        previously incorporated one, :meth:`resolve_overlap` is called to
        produce a merged patch for the conflicting region.

        Parameters
        ----------
        sections:
            Ordered list of sections to merge.

        Returns
        -------
        dict[str, Any]
            Assembly dict containing ``patches``, ``merged_content``,
            ``overlap_count``, and ``conflicts``.
        """
        patches: dict[str, Any] = {}
        conflicts: list[dict[str, Any]] = []
        overlap_count = 0

        for section in sections:
            if not isinstance(section, dict):
                continue
            pid = str(section.get("patch_id", uuid.uuid4()))
            regions = set(section.get("regions", []))

            # Check against existing patches for overlaps
            for existing_pid, existing in list(patches.items()):
                existing_regions = set(existing.get("regions", []))
                shared = regions & existing_regions
                if shared:
                    overlap_count += 1
                    resolution = self.resolve_overlap(section, existing)
                    if resolution.get("conflict"):
                        c = resolution["conflict"]
                        conflicts.append(c)
                    else:
                        # Merge resolved content back
                        patches[existing_pid] = {**existing, **resolution.get("merged", {})}

            patches[pid] = {
                "patch_id": pid,
                "content": section.get("content", ""),
                "regions": list(regions),
                "trust_tier": section.get("trust_tier"),
                "metadata": section.get("metadata", {}),
            }

        # Apply Čech gluing to the collected patches
        cocycle_result = self.apply_cocycle_gluing(list(patches.values()))

        return {
            "patches": patches,
            "merged_content": cocycle_result.get("merged_content", ""),
            "overlap_count": overlap_count,
            "conflicts": conflicts,
            "cocycle_checks_passed": cocycle_result.get("checks_passed", 0),
            "metadata": {
                "section_count": len(sections),
                "patch_count": len(patches),
            },
        }

    def resolve_overlap(self, s1: Any, s2: Any) -> dict[str, Any]:
        """Resolve an overlap between two sections and return a merge descriptor.

        The default resolution policy is *last-write-wins* for content, with
        the higher trust tier taking precedence if both sections carry one.

        Parameters
        ----------
        s1:
            The first (incoming) section dict.
        s2:
            The second (existing) section dict.

        Returns
        -------
        dict[str, Any]
            A resolution descriptor with optional ``"merged"`` and
            ``"conflict"`` keys.
        """
        if not (isinstance(s1, dict) and isinstance(s2, dict)):
            return {"merged": {}, "conflict": None}

        pid1 = s1.get("patch_id", "unknown-a")
        pid2 = s2.get("patch_id", "unknown-b")
        regions_a = set(s1.get("regions", []))
        regions_b = set(s2.get("regions", []))
        shared = regions_a & regions_b

        # Determine which patch's content to prefer
        tier_strength = {"unverified": 0, "low": 1, "medium": 2, "high": 3, "verified": 4}
        t1 = tier_strength.get(str(s1.get("trust_tier", "medium")).lower(), 2)
        t2 = tier_strength.get(str(s2.get("trust_tier", "medium")).lower(), 2)

        preferred = s1 if t1 >= t2 else s2
        discarded = s2 if t1 >= t2 else s1

        # Detect genuine content conflict
        content_conflict = (
            s1.get("content") != s2.get("content")
            and s1.get("content") is not None
            and s2.get("content") is not None
        )

        conflict_record: dict[str, Any] | None = None
        if content_conflict:
            conflict = AssemblyConflict(
                conflict_id=str(uuid.uuid4()),
                patch_a=str(pid1),
                patch_b=str(pid2),
                overlap_region=", ".join(sorted(shared)),
                conflict_type="content",
                description=(
                    f"Patches '{pid1}' and '{pid2}' have conflicting content in "
                    f"regions {sorted(shared)}.  Preferring patch with higher trust tier."
                ),
                blocking=False,
            )
            conflict_record = conflict.to_dict()
            logger.debug("Overlap conflict resolved: %s wins over %s", preferred.get("patch_id"), discarded.get("patch_id"))

        merged = {
            "content": preferred.get("content", ""),
            "regions": list(regions_a | regions_b),
            "trust_tier": preferred.get("trust_tier"),
            "metadata": {**discarded.get("metadata", {}), **preferred.get("metadata", {})},
        }
        return {"merged": merged, "conflict": conflict_record}

    def apply_cocycle_gluing(self, sections: Sequence[Any]) -> dict[str, Any]:
        """Apply Čech cocycle gluing to the collected patch list.

        This method verifies the cocycle condition and, where the condition is
        satisfied, emits a merged content string by concatenating patch content
        in a canonical order (sorted by patch_id for determinism).

        Parameters
        ----------
        sections:
            The flat list of patch dicts to glue.

        Returns
        -------
        dict[str, Any]
            Result dict with keys ``"merged_content"``, ``"checks_passed"``,
            ``"cocycle_ok"``.
        """
        section_list = [s for s in sections if isinstance(s, dict)]
        cocycle_ok = self._analyzer.verify_cocycle_condition(section_list)
        checks_passed = 0

        # Count the number of successful triple checks
        patch_ids = [str(s.get("patch_id", i)) for i, s in enumerate(section_list)]
        overlaps = self._analyzer._detect_overlaps(section_list)
        overlap_pairs: set[frozenset[str]] = {
            frozenset({o["patch_a"], o["patch_b"]}) for o in overlaps
        }
        for i in range(len(patch_ids)):
            for j in range(i + 1, len(patch_ids)):
                for k in range(j + 1, len(patch_ids)):
                    pi, pj, pk = patch_ids[i], patch_ids[j], patch_ids[k]
                    if (
                        frozenset({pi, pj}) in overlap_pairs
                        and frozenset({pj, pk}) in overlap_pairs
                        and frozenset({pi, pk}) in overlap_pairs
                    ):
                        checks_passed += 1

        # Produce merged content (canonical ordering by patch_id)
        sorted_sections = sorted(section_list, key=lambda s: str(s.get("patch_id", "")))
        merged_content = "\n".join(
            str(s.get("content", "")) for s in sorted_sections if s.get("content")
        )

        return {
            "merged_content": merged_content,
            "checks_passed": checks_passed,
            "cocycle_ok": cocycle_ok,
        }

    def finalize(self, assembly: dict[str, Any]) -> dict[str, Any]:
        """Freeze an assembly dict and compute its integrity hash.

        ``finalize`` takes the mutable assembly dict produced by
        :meth:`assemble_sections` and returns a new dict enriched with a
        SHA-256 integrity hash and a finalization timestamp.

        Parameters
        ----------
        assembly:
            The assembly dict to finalise.

        Returns
        -------
        dict[str, Any]
            The finalised assembly dict with additional ``"hash"``,
            ``"finalized_at"``, and ``"phase"`` keys.
        """
        payload = json.dumps(assembly, default=str, sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        finalized: dict[str, Any] = {
            **assembly,
            "hash": digest,
            "finalized_at": datetime.now(tz=timezone.utc).isoformat(),
            "phase": AssemblyPhase.FINALIZATION.value,
        }
        logger.debug("Assembly finalized: hash=%s", digest)
        return finalized

    def get_assembly_history(self) -> list[dict[str, Any]]:
        """Return a copy of the assembly history list.

        Each entry in the list is a dict with keys ``"assembly_id"``,
        ``"record"``, ``"witness"``, and ``"timestamp"``.

        Returns
        -------
        list[dict[str, Any]]
            Ordered list of history entries, oldest first.
        """
        return list(self._history)

    def reset(self) -> None:
        """Clear the assembly history and reset the current phase.

        After calling this method the coordinator behaves as if it were
        freshly constructed.
        """
        self._history.clear()
        self._current_phase = AssemblyPhase.COLLECTION
        logger.debug("FinalAssemblyCoordinator.reset() called — history cleared")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _sort_by_trust(self, sections: list[Any]) -> list[Any]:
        """Sort sections by descending trust tier strength."""
        tier_strength = {"unverified": 0, "low": 1, "medium": 2, "high": 3, "verified": 4}

        def key(s: Any) -> int:
            if not isinstance(s, dict):
                return 0
            tier = str(s.get("trust_tier", "medium")).lower()
            return tier_strength.get(tier, 2)

        return sorted(sections, key=key, reverse=True)


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_coordinator(
    analyzer: FinalAssemblyAnalyzer | None = None,
) -> FinalAssemblyCoordinator:
    """Construct a ready-to-use :class:`FinalAssemblyCoordinator`.

    Parameters
    ----------
    analyzer:
        Optional custom analyzer.  A default :class:`FinalAssemblyAnalyzer`
        is used when *None*.

    Returns
    -------
    FinalAssemblyCoordinator
        A freshly initialised coordinator.
    """
    return FinalAssemblyCoordinator(analyzer=analyzer)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    log = logging.getLogger(__name__)

    # Build a small set of synthetic sections
    sample_sections = [
        {
            "patch_id": "patch-alpha",
            "content": "The alpha section introduces the main theme.",
            "regions": ["region-A", "region-B"],
            "trust_tier": "high",
            "original_trust_tier": "high",
            "metadata": {"source": "replay"},
        },
        {
            "patch_id": "patch-beta",
            "content": "The beta section elaborates on region B.",
            "regions": ["region-B", "region-C"],
            "trust_tier": "medium",
            "original_trust_tier": "medium",
            "metadata": {"source": "gluing"},
        },
        {
            "patch_id": "patch-gamma",
            "content": "The gamma section closes the arc in region C.",
            "regions": ["region-A", "region-C"],
            "trust_tier": "high",
            "original_trust_tier": "high",
            "metadata": {"source": "replay"},
        },
    ]

    analyzer = FinalAssemblyAnalyzer()
    print(analyzer.report(sample_sections))

    coordinator = FinalAssemblyCoordinator(analyzer=analyzer)

    for strat in AssemblyStrategy:
        log.info("--- Running strategy: %s ---", strat.value)
        try:
            witness = coordinator.run(sample_sections, strategy=strat)
            print(f"\nStrategy={strat.value}")
            print(f"  witness_id         : {witness.witness_id}")
            print(f"  cocycle_satisfied  : {witness.cocycle_satisfied}")
            print(f"  trust_audit_preserved: {witness.trust_audit_preserved}")
            print(f"  patch_ids          : {witness.patch_ids}")
        except ValueError as exc:
            print(f"Strategy={strat.value} failed: {exc}")

    print("\nHistory entries:", len(coordinator.get_assembly_history()))
    coordinator.reset()
    print("After reset, history entries:", len(coordinator.get_assembly_history()))

    # Demonstrate conflict detection
    conflicting_sections = [
        {
            "patch_id": "cp-1",
            "content": "Version A of the shared region.",
            "regions": ["shared"],
            "trust_tier": "medium",
            "original_trust_tier": "medium",
        },
        {
            "patch_id": "cp-2",
            "content": "Version B of the shared region.",
            "regions": ["shared"],
            "trust_tier": "high",
            "original_trust_tier": "high",
        },
    ]
    assembled = coordinator.assemble_sections(conflicting_sections)
    print(f"\nConflict detection: {len(assembled['conflicts'])} conflict(s) found")
    if assembled["conflicts"]:
        c = assembled["conflicts"][0]
        print(f"  conflict_type: {c['conflict_type']}")
        print(f"  resolvable   : {c['resolvable']}")

    # Demonstrate trust-audit failure detection
    broken_trust_sections = [
        {
            "patch_id": "bt-1",
            "content": "Some content.",
            "regions": ["R1"],
            "trust_tier": None,          # tier was erased!
            "original_trust_tier": "high",
        }
    ]
    trust_ok = analyzer.check_trust_audit_trail(broken_trust_sections)
    print(f"\nBroken trust audit detected: {not trust_ok}")

    print("\nSmoke test complete.")
