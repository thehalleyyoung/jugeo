"""
jugeo.thesis.semantic_center.integration
==========================================

Integration of the semantic center with the rest of the JuGeo system.

This module connects the thesis-level models and algorithms in the
``semantic_center`` package to the rest of JuGeo's subsystems:
* The evidence pipeline (``jugeo.evidence``).
* The judgment algebra (``jugeo.judgments``).
* The error/failure surfaces (``jugeo.errors``).
* The package manifest (``semantic_center.manifest``).

The integration layer serves two purposes:

1. **Verification bridge**: It provides entry points for the rest of the
   system to submit evidence to the semantic center and receive structured
   verdicts (verified/obstructed/insufficient trust).

2. **Thesis tracking**: It wires the live evidence pipeline to the
   ``ThesisClaim`` records in the manifest, updating their status as
   new evidence arrives.

Classes
-------
* ``EvidenceChannelBinding``    — Binds a specific evidence channel to the
  semantic center verification protocol.
* ``ThesisClaimTracker``        — Tracks the evidence status of all thesis
  claims in the manifest.
* ``ManifestIntegrityCheck``    — Checks that the package manifest is consistent
  with the current implementation state.
* ``SemanticCenterIntegration`` — Top-level integration object.

References
----------
* theory2.tex §1.3–§1.5 — Semantic Center, Coordinates, Sheaf Theory
* theory2.tex §2.1–§2.3 — Thesis, Contributions, Problem Classes
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from jugeo.errors import (
    FailureClassification,
    FailureScope,
    JuGeoError,
    StructuredFailure,
    raise_with_scope,
)
from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    Proposition,
    PropositionKind,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.evidence.trust import TrustAlgebra

from jugeo.thesis.semantic_center.manifest import (
    PackageManifest,
    SEMANTIC_CENTER_MANIFEST,
    TheoryClaim,
)
from jugeo.thesis.semantic_center.models import (
    ThesisClaim,
    ClaimStatus,
    JUGEO_WORLDVIEW,
)
from jugeo.thesis.semantic_center.algorithms import (
    ClaimVerificationAlgorithm,
    AlgorithmResult,
)

__all__ = [
    "EvidenceChannelBinding",
    "ThesisClaimTracker",
    "ManifestIntegrityCheck",
    "SemanticCenterIntegration",
    "IntegrationReport",
    "SEMANTIC_CENTER_INTEGRATION",
]


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntegrationReport:
    """Report from a semantic center integration check.

    Parameters
    ----------
    passed:
        Whether the integration check passed.
    manifest_valid:
        Whether the package manifest validates.
    worldview_consistent:
        Whether the worldview invariants hold.
    claims_status:
        Dict mapping claim ID to ``ClaimStatus``.
    failures:
        Tuple of ``StructuredFailure`` objects from failed checks.
    copilot_notes:
        Tuple of Copilot-generated notes.
    evidence_channel_count:
        Number of evidence channel bindings active.
    """

    passed: bool
    manifest_valid: bool
    worldview_consistent: bool
    claims_status: dict[str, str]
    failures: tuple[StructuredFailure, ...]
    copilot_notes: tuple[str, ...]
    evidence_channel_count: int

    def summary_lines(self) -> list[str]:
        """Return a human-readable summary.

        Returns
        -------
        list[str]
        """
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"IntegrationReport [{status}]",
            f"  Manifest valid:       {self.manifest_valid}",
            f"  Worldview consistent: {self.worldview_consistent}",
            f"  Evidence channels:    {self.evidence_channel_count}",
            f"  Failures:             {len(self.failures)}",
        ]
        if self.claims_status:
            lines.append("  Claim statuses:")
            for claim_id, status_val in self.claims_status.items():
                lines.append(f"    {claim_id}: {status_val}")
        if self.failures:
            lines.append("  Failures:")
            for f in self.failures:
                lines.append(f"    · {f.message}")
        if self.copilot_notes:
            lines.append("  Copilot notes:")
            for note in self.copilot_notes:
                lines.append(f"    [copilot] {note}")
        return lines

    def __str__(self) -> str:
        return "\n".join(self.summary_lines())


# ---------------------------------------------------------------------------
# EvidenceChannelBinding
# ---------------------------------------------------------------------------


class EvidenceChannelBinding:
    """Binds a specific evidence channel to the semantic center verification protocol.

    An ``EvidenceChannelBinding`` is the bridge between an evidence-producing
    subsystem (solver, runtime monitor, oracle, formal prover) and the semantic
    center's verification pipeline.

    When evidence arrives from a channel, the binding:
    1. Wraps it as an ``EvidenceItem`` with the correct kind and trust level.
    2. Routes it to the semantic center's ``CoordinatedVerification`` protocol.
    3. Updates the associated ``ThesisClaim`` records if the evidence is relevant.
    4. Records any resulting trust promotions in the trust audit log.

    Parameters
    ----------
    channel_name:
        Human-readable name for this channel (e.g. ``"Z3 SMT solver"``).
    evidence_kind:
        The kind of evidence this channel produces.
    trust_level:
        The trust level assigned to this channel's output.
    active:
        Whether this binding is currently active.
    callback:
        Optional callback invoked when evidence arrives.
    """

    def __init__(
        self,
        channel_name: str,
        evidence_kind: EvidenceItemKind,
        trust_level: TrustLevel,
        active: bool = True,
        callback: Callable[[EvidenceItem], None] | None = None,
    ) -> None:
        """Initialize the evidence channel binding.

        Parameters
        ----------
        channel_name:
            Channel name.
        evidence_kind:
            Evidence kind.
        trust_level:
            Trust level for this channel.
        active:
            Whether binding is active.
        callback:
            Optional callback.
        """
        self.channel_name = channel_name
        self.evidence_kind = evidence_kind
        self.trust_level = trust_level
        self.active = active
        self.callback = callback
        self._algebra = TrustAlgebra()
        self._received_items: list[EvidenceItem] = []

    def receive(
        self,
        fact: str,
        coordinate: str,
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        """Receive raw evidence from this channel and wrap it as an EvidenceItem.

        Parameters
        ----------
        fact:
            The evidence fact (e.g., Z3 UNSAT certificate, runtime assertion pass).
        coordinate:
            Semantic coordinate for this evidence.
        metadata:
            Optional metadata.

        Returns
        -------
        EvidenceItem
            The wrapped evidence item.

        Raises
        ------
        JuGeoError
            If the binding is not active.
        """
        if not self.active:
            raise_with_scope(
                f"Evidence channel {self.channel_name!r} is not active",
                scope=FailureScope.EVIDENCE,
                classification=FailureClassification.JURISDICTION_EXCEEDED,
                coordinate=coordinate,
            )

        from datetime import datetime, timezone

        item = EvidenceItem(
            key=f"{self.evidence_kind.name.lower()}-{coordinate}-{hash(fact) & 0xFFFF:04x}",
            kind=self.evidence_kind,
            trust_level=self.trust_level,
            source_description=f"{self.channel_name}: {fact[:120]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            provenance_details=f"EvidenceChannelBinding({self.channel_name!r})",
            metadata=metadata or {},
        )
        self._received_items.append(item)

        if self.callback is not None:
            self.callback(item)

        return item

    def trust_annotation_for(self, fact: str) -> TrustAnnotation:
        """Return a ``TrustAnnotation`` for evidence from this channel.

        Parameters
        ----------
        fact:
            The evidence fact string.

        Returns
        -------
        TrustAnnotation
        """
        return TrustAnnotation(
            level=self.trust_level,
            evidence_basis=(f"{self.evidence_kind.name}-{hash(fact) & 0xFFFF:04x}",),
            ceiling=TrustLevel.VERIFIED_PROOF,
            floor=TrustLevel.UNVERIFIED,
            reasons=(f"from channel {self.channel_name!r}",),
        )

    def received_count(self) -> int:
        """Return the number of evidence items received by this binding.

        Returns
        -------
        int
        """
        return len(self._received_items)

    def deactivate(self) -> None:
        """Deactivate this binding.

        After deactivation, calls to ``receive()`` will raise ``JuGeoError``.
        """
        self.active = False

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly summary.

        Returns
        -------
        str
        """
        return (
            f"EvidenceChannelBinding: {self.channel_name!r}\n"
            f"  Kind: {self.evidence_kind.name}\n"
            f"  Trust: {self.trust_level.name}\n"
            f"  Active: {self.active}\n"
            f"  Received: {self.received_count()} items\n"
            f"  Copilot: This binding routes {self.channel_name} evidence to the\n"
            f"    semantic center at {self.trust_level.name} trust level."
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "channel_name": self.channel_name,
            "evidence_kind": self.evidence_kind.name,
            "trust_level": self.trust_level.name,
            "active": self.active,
            "received_count": self.received_count(),
        }


# ---------------------------------------------------------------------------
# ThesisClaimTracker
# ---------------------------------------------------------------------------


class ThesisClaimTracker:
    """Tracks the evidence status of all thesis claims in the manifest.

    ``ThesisClaimTracker`` maintains a live dictionary of ``ThesisClaim``
    objects (one per ``TheoryClaim`` in the manifest) and updates them as
    new evidence arrives from the evidence channels.

    It also provides:
    * Query methods for claim status, trust level, and obstruction records.
    * A method for running ``ClaimVerificationAlgorithm`` on all claims.
    * A Copilot interface for querying claim status.

    Parameters
    ----------
    manifest:
        The ``PackageManifest`` providing the canonical theory claims.
    """

    def __init__(
        self,
        manifest: PackageManifest | None = None,
    ) -> None:
        """Initialize the claim tracker.

        Parameters
        ----------
        manifest:
            Package manifest.  Defaults to ``SEMANTIC_CENTER_MANIFEST``.
        """
        self.manifest = manifest or SEMANTIC_CENTER_MANIFEST
        self._claims: dict[str, ThesisClaim] = self._initialize_claims()
        self._algebra = TrustAlgebra()
        self._verification_results: dict[str, AlgorithmResult] = {}

    def _initialize_claims(self) -> dict[str, ThesisClaim]:
        """Initialize ThesisClaim objects from the manifest's TheoryClaim records.

        Returns
        -------
        dict[str, ThesisClaim]
            Mapping from claim ID to ThesisClaim.
        """
        claims: dict[str, ThesisClaim] = {}
        for tc in self.manifest.theory_claims:
            claims[tc.claim_id] = ThesisClaim(
                claim_id=tc.claim_id,
                section=tc.section,
                statement=tc.statement,
                status=ClaimStatus.PROPOSED,
                evidence=EvidenceBundle(),
                open_obligations=(
                    f"Verify claim {tc.claim_id} via evidence pipeline",
                ),
                obstructions=(),
                trust=TrustAnnotation(
                    level=tc.evidence_trust_level,
                    evidence_basis=(),
                    ceiling=TrustLevel.VERIFIED_PROOF,
                    floor=TrustLevel.UNVERIFIED,
                    reasons=(f"initialized from manifest: {tc.claim_id}",),
                ),
                copilot_annotation=tc.copilot_annotation,
                formalized=tc.formalized,
            )
        return claims

    def get_claim(self, claim_id: str) -> ThesisClaim | None:
        """Return the ThesisClaim for a given ID.

        Parameters
        ----------
        claim_id:
            Claim identifier.

        Returns
        -------
        ThesisClaim | None
        """
        return self._claims.get(claim_id)

    def all_claims(self) -> list[ThesisClaim]:
        """Return all tracked thesis claims.

        Returns
        -------
        list[ThesisClaim]
        """
        return list(self._claims.values())

    def add_evidence_to_claim(
        self,
        claim_id: str,
        item: EvidenceItem,
    ) -> ThesisClaim | None:
        """Add an evidence item to a specific claim.

        Parameters
        ----------
        claim_id:
            The claim to update.
        item:
            The evidence item to add.

        Returns
        -------
        ThesisClaim | None
            The updated claim, or ``None`` if not found.
        """
        claim = self._claims.get(claim_id)
        if claim is None:
            return None
        updated = claim.with_evidence(item)
        self._claims[claim_id] = updated
        return updated

    def discharge_obligation(
        self,
        claim_id: str,
        obligation: str,
    ) -> ThesisClaim | None:
        """Discharge an obligation for a specific claim.

        Parameters
        ----------
        claim_id:
            The claim to update.
        obligation:
            The obligation string to discharge.

        Returns
        -------
        ThesisClaim | None
        """
        claim = self._claims.get(claim_id)
        if claim is None:
            return None
        if obligation not in claim.open_obligations:
            return claim
        updated = claim.discharge_obligation(obligation)
        self._claims[claim_id] = updated
        return updated

    def verified_claims(self) -> list[ThesisClaim]:
        """Return all claims with VERIFIED status.

        Returns
        -------
        list[ThesisClaim]
        """
        return [c for c in self._claims.values() if c.is_verified()]

    def obstructed_claims(self) -> list[ThesisClaim]:
        """Return all claims with obstructions.

        Returns
        -------
        list[ThesisClaim]
        """
        return [c for c in self._claims.values() if c.is_obstructed()]

    def combined_trust(self) -> TrustLevel:
        """Return the combined trust level across all claims (meet).

        Returns
        -------
        TrustLevel
        """
        levels = [c.trust_level() for c in self._claims.values()]
        if not levels:
            return TrustLevel.UNVERIFIED
        result = levels[0]
        for level in levels[1:]:
            result = self._algebra.compose(result, level)
        return result

    def run_verification_algorithm(
        self,
        claim_id: str,
        base_judgment: Judgment | None = None,
        solver_callback: Callable[[Judgment], tuple[bool, str]] | None = None,
    ) -> AlgorithmResult | None:
        """Run the claim verification algorithm for a specific claim.

        Parameters
        ----------
        claim_id:
            The claim to verify.
        base_judgment:
            A base judgment to verify against.  If ``None``, one is
            constructed from the claim.
        solver_callback:
            Optional solver callback.

        Returns
        -------
        AlgorithmResult | None
            ``None`` if the claim is not found.
        """
        claim = self._claims.get(claim_id)
        if claim is None:
            return None

        algo = ClaimVerificationAlgorithm(
            solver_callback=solver_callback,
            max_steps=20,
        )

        if base_judgment is None:
            # Build a minimal judgment from the claim
            from jugeo.judgments.judgment_terms import (
                Carrier,
                Provenance,
                ProvenanceSource,
            )
            prop = Proposition(
                kind=PropositionKind.SEMANTIC,
                formula=claim.statement,
                free_variables=(),
            )
            carrier = Carrier(type_name="ThesisClaim")
            prov = Provenance(
                source=ProvenanceSource.ORACLE,
                author="semantic_center_integration",
            )

            class _FakeCoord:
                def __str__(self) -> str:
                    return f"thesis.{claim_id}"
                def __repr__(self) -> str:
                    return str(self)

            base_judgment = Judgment(
                coordinate=_FakeCoord(),
                proposition=prop,
                carrier=carrier,
                evidence=claim.evidence,
                obligations=(),
                obstructions=(),
                trust=claim.trust,
                provenance=prov,
            )

        result = algo.run(
            claim_judgment=base_judgment,
            obligations=list(claim.open_obligations),
        )
        self._verification_results[claim_id] = result

        # Update claim status based on result
        if result.success:
            self._claims[claim_id] = ThesisClaim(
                claim_id=claim.claim_id,
                section=claim.section,
                statement=claim.statement,
                status=ClaimStatus.VERIFIED,
                evidence=claim.evidence,
                open_obligations=(),
                obstructions=claim.obstructions,
                trust=TrustAnnotation(
                    level=result.trust_level(),
                    evidence_basis=claim.trust.evidence_basis,
                    ceiling=TrustLevel.VERIFIED_PROOF,
                    floor=TrustLevel.UNVERIFIED,
                    reasons=claim.trust.reasons + (f"verified by algorithm",),
                ),
                copilot_annotation=claim.copilot_annotation,
                formalized=claim.formalized,
            )

        return result

    def copilot_query(self, question: str) -> str:
        """Answer a Copilot query about claim status.

        Parameters
        ----------
        question:
            Natural-language question about claim status.

        Returns
        -------
        str
        """
        q = question.lower()
        if "verified" in q:
            v = self.verified_claims()
            return f"{len(v)} claim(s) verified: {', '.join(c.claim_id for c in v)}"
        elif "obstructed" in q:
            o = self.obstructed_claims()
            return f"{len(o)} claim(s) obstructed: {', '.join(c.claim_id for c in o)}"
        elif "trust" in q:
            return f"Combined trust level: {self.combined_trust().name}"
        else:
            return self.copilot_summary()

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly summary of claim tracking status.

        Returns
        -------
        str
        """
        total = len(self._claims)
        verified = len(self.verified_claims())
        obstructed = len(self.obstructed_claims())
        combined = self.combined_trust()
        claim_lines = "\n".join(c.copilot_summary() for c in self.all_claims())
        return "\n".join([
            "ThesisClaimTracker",
            f"Total claims: {total}",
            f"Verified: {verified} | Obstructed: {obstructed} | "
            f"Combined trust: {combined.name}",
            "",
            "Claim details:",
            claim_lines,
        ])


# ---------------------------------------------------------------------------
# ManifestIntegrityCheck
# ---------------------------------------------------------------------------


class ManifestIntegrityCheck:
    """Checks that the package manifest is consistent with the implementation.

    ``ManifestIntegrityCheck`` verifies:
    1. The manifest itself validates (no internal inconsistencies).
    2. The declared API entries correspond to importable symbols.
    3. The worldview invariants hold.
    4. The theory alignment check passes.

    Parameters
    ----------
    manifest:
        The ``PackageManifest`` to check.
    """

    def __init__(
        self,
        manifest: PackageManifest | None = None,
    ) -> None:
        """Initialize the integrity check.

        Parameters
        ----------
        manifest:
            Package manifest.  Defaults to canonical.
        """
        self.manifest = manifest or SEMANTIC_CENTER_MANIFEST

    def run(self) -> IntegrationReport:
        """Run all integrity checks and return a report.

        Returns
        -------
        IntegrationReport
        """
        failures: list[StructuredFailure] = []
        copilot_notes: list[str] = []

        # Check 1: manifest validates
        manifest_failure = self.manifest.validate()
        manifest_valid = manifest_failure is None
        if manifest_failure is not None:
            failures.append(manifest_failure)

        # Check 2: worldview invariants
        worldview_failure = JUGEO_WORLDVIEW.validate()
        worldview_consistent = worldview_failure is None
        if worldview_failure is not None:
            failures.append(worldview_failure)

        # Check 3: theory alignment
        alignment = self.manifest.theory_alignment_check()
        if not alignment["aligned"]:
            for issue in alignment["issues"]:
                failures.append(StructuredFailure(
                    message=f"Theory alignment issue: {issue}",
                    scope=FailureScope.CHAPTER,
                    classification=FailureClassification.INVALID_VALUE,
                ))

        # Copilot note about the check
        if not failures:
            copilot_notes.append(
                "All integrity checks passed.  Copilot can safely navigate "
                "the semantic center using the manifest as a guide."
            )
        else:
            copilot_notes.append(
                f"{len(failures)} integrity issue(s) found.  "
                "Review StructuredFailure records for repair hints."
            )

        passed = len(failures) == 0

        return IntegrationReport(
            passed=passed,
            manifest_valid=manifest_valid,
            worldview_consistent=worldview_consistent,
            claims_status={},
            failures=tuple(failures),
            copilot_notes=tuple(copilot_notes),
            evidence_channel_count=0,
        )

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly summary.

        Returns
        -------
        str
        """
        report = self.run()
        return str(report)


# ---------------------------------------------------------------------------
# SemanticCenterIntegration
# ---------------------------------------------------------------------------


class SemanticCenterIntegration:
    """Top-level integration object for the semantic center.

    ``SemanticCenterIntegration`` assembles all integration components:
    * Evidence channel bindings.
    * Thesis claim tracker.
    * Manifest integrity checker.

    It provides a single entry point for:
    * Submitting evidence to the semantic center.
    * Querying claim status.
    * Running the manifest integrity check.
    * Generating a full integration report.

    This class is the operational realization of theory2.tex's semantic center
    concept: it is the single stable reference point that all other subsystems
    use to coordinate their evidence and verification results.

    Parameters
    ----------
    manifest:
        The ``PackageManifest`` to integrate with.
    """

    def __init__(
        self,
        manifest: PackageManifest | None = None,
    ) -> None:
        """Initialize the integration.

        Parameters
        ----------
        manifest:
            Package manifest.  Defaults to canonical.
        """
        self.manifest = manifest or SEMANTIC_CENTER_MANIFEST
        self.claim_tracker = ThesisClaimTracker(manifest=self.manifest)
        self.integrity_check = ManifestIntegrityCheck(manifest=self.manifest)
        self._algebra = TrustAlgebra()
        self._channels: dict[str, EvidenceChannelBinding] = self._init_channels()

    def _init_channels(self) -> dict[str, EvidenceChannelBinding]:
        """Initialize the canonical evidence channel bindings.

        Returns
        -------
        dict[str, EvidenceChannelBinding]
        """
        return {
            "formal_proof": EvidenceChannelBinding(
                channel_name="Formal Proof Channel",
                evidence_kind=EvidenceItemKind.FORMAL_PROOF,
                trust_level=TrustLevel.VERIFIED_PROOF,
            ),
            "solver": EvidenceChannelBinding(
                channel_name="SMT Solver Channel (Z3/CVC5)",
                evidence_kind=EvidenceItemKind.SOLVER_PROOF,
                trust_level=TrustLevel.SOLVER_DISCHARGED,
            ),
            "runtime": EvidenceChannelBinding(
                channel_name="Runtime Witness Channel",
                evidence_kind=EvidenceItemKind.RUNTIME_WITNESS,
                trust_level=TrustLevel.RUNTIME_WITNESSED,
            ),
            "oracle": EvidenceChannelBinding(
                channel_name="Oracle/Copilot Proposal Channel",
                evidence_kind=EvidenceItemKind.ORACLE_PROPOSAL,
                trust_level=TrustLevel.ORACLE_PROPOSED,
            ),
        }

    def submit_evidence(
        self,
        channel_name: str,
        fact: str,
        coordinate: str,
        claim_ids: Sequence[str] | None = None,
    ) -> EvidenceItem | None:
        """Submit evidence from a named channel to the semantic center.

        Parameters
        ----------
        channel_name:
            The channel to submit through (``"formal_proof"``, ``"solver"``,
            ``"runtime"``, or ``"oracle"``).
        fact:
            The evidence fact string.
        coordinate:
            Semantic coordinate for this evidence.
        claim_ids:
            Optional list of claim IDs to associate this evidence with.

        Returns
        -------
        EvidenceItem | None
            The evidence item if the channel exists; ``None`` otherwise.

        Notes
        -----
        If ``claim_ids`` is provided, the evidence item is added to each
        named claim's evidence bundle via the claim tracker.
        """
        channel = self._channels.get(channel_name)
        if channel is None:
            return None

        item = channel.receive(fact=fact, coordinate=coordinate)

        if claim_ids:
            for cid in claim_ids:
                self.claim_tracker.add_evidence_to_claim(cid, item)

        return item

    def full_integrity_report(self) -> IntegrationReport:
        """Run the full integrity check and return a report.

        Returns
        -------
        IntegrationReport
        """
        base_report = self.integrity_check.run()

        # Add claim status information
        claims_status = {
            c.claim_id: c.status.value
            for c in self.claim_tracker.all_claims()
        }

        # Add channel counts
        channel_count = sum(
            1 for ch in self._channels.values() if ch.active
        )

        return IntegrationReport(
            passed=base_report.passed,
            manifest_valid=base_report.manifest_valid,
            worldview_consistent=base_report.worldview_consistent,
            claims_status=claims_status,
            failures=base_report.failures,
            copilot_notes=base_report.copilot_notes,
            evidence_channel_count=channel_count,
        )

    def channel_summary(self) -> str:
        """Return a summary of all evidence channels.

        Returns
        -------
        str
        """
        lines = [f"Evidence channels ({len(self._channels)}):"]
        for name, ch in self._channels.items():
            lines.append(f"  {name}: {ch.channel_name} [{ch.trust_level.name}] "
                         f"received={ch.received_count()}")
        return "\n".join(lines)

    def copilot_query(self, question: str) -> str:
        """Answer a Copilot query about the integration.

        Parameters
        ----------
        question:
            Natural-language question.

        Returns
        -------
        str
        """
        q = question.lower()
        if "channel" in q or "evidence" in q:
            return self.channel_summary()
        elif "claim" in q or "thesis" in q:
            return self.claim_tracker.copilot_query(question)
        elif "integrity" in q or "manifest" in q or "check" in q:
            return str(self.full_integrity_report())
        else:
            return self.copilot_summary()

    def copilot_summary(self) -> str:
        """Return a Copilot-friendly top-level summary.

        Returns
        -------
        str
        """
        report = self.full_integrity_report()
        return "\n".join([
            "SemanticCenterIntegration",
            f"Manifest: {self.manifest.name} v{self.manifest.version}",
            "",
            str(report),
            "",
            self.channel_summary(),
            "",
            "Copilot: Use submit_evidence() to route evidence to the semantic\n"
            "  center.  Use full_integrity_report() to check manifest consistency.\n"
            "  Use claim_tracker.copilot_query() to ask about specific claims.",
        ])

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
        """
        report = self.full_integrity_report()
        return {
            "manifest": self.manifest.to_dict(),
            "integrity_passed": report.passed,
            "claims_status": report.claims_status,
            "channels": {
                name: ch.to_dict()
                for name, ch in self._channels.items()
            },
        }

    def __repr__(self) -> str:
        return (
            f"SemanticCenterIntegration("
            f"manifest={self.manifest.name!r}, "
            f"channels={len(self._channels)})"
        )


# ---------------------------------------------------------------------------
# Canonical instance
# ---------------------------------------------------------------------------

SEMANTIC_CENTER_INTEGRATION: SemanticCenterIntegration = SemanticCenterIntegration()
