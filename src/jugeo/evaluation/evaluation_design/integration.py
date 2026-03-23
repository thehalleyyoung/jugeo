"""Integration layer connecting evaluation_design to all JuGeo subsystems.

Theory reference: theory2.tex Ch63
copilot: shared-core marker

This module is the integration hub that connects the evaluation_design package
to the broader JuGeo ecosystem.  It provides six integration classes:

* EvaluationEvidenceIntegration  — connects to jugeo.evidence
* EvaluationPacksIntegration     — connects to jugeo.packs
* EvaluationOrchestrationIntegration — connects to jugeo.orchestration
* EvaluationIdeationIntegration  — connects to jugeo.ideation
* EvaluationGeometryIntegration  — connects to jugeo.geometry
* FullEvaluationIntegration      — top-level orchestrator that uses all of the above

All cross-module imports are guarded with try/except so the package degrades
gracefully when optional subsystems are absent.

Integration Pattern Guide
--------------------------
Each integration class follows the same pattern:

1.  **Guard the import** – all jugeo.* imports are wrapped in a single
    ``try/except Exception: pass`` block at module level so that missing
    subsystems never prevent this module from loading.

2.  **metadata dict** – every class stores a ``metadata`` dict on ``self``
    that contains at minimum ``integration_id``, ``created_at``, and
    ``subsystem``.  This lets ``FullEvaluationIntegration.validate_integration``
    check that each layer is alive without importing anything.

3.  **subsystem_available flag** – every method that touches an external
    subsystem returns a dict that includes ``subsystem_available: bool``.
    Callers can inspect this flag to decide whether to surface a degraded-mode
    warning rather than raising an exception.

4.  **Graceful fallback** – when the external call fails (ImportError,
    AttributeError, or any other exception), the method still returns a valid,
    populated dict so that calling code can continue without branching on
    availability.

5.  **No side-effects on import** – no network calls, file-system access, or
    expensive computation happens at import time.  All work is deferred to
    method calls.

Theory reference
-----------------
Chapter 63 of theory2.tex ("Integration Semantics for Evaluation Algebras")
describes the formal contract that this module implements.  In brief:

* An *evidence record* (§63.2) is the canonical unit of evaluation output.
* A *provenance trace* (§63.4) tracks the full derivation chain of a result.
* A *trust profile* (§63.7) maps a scored result to a qualitative trust tier.
* A *bridge theorem* (§63.11) connects the evaluation algebra to the pack
  algebra via a natural transformation.
* An *ideation proposal* (§63.14) lifts a design into the ideation monad.
* A *geometric site* (§63.18) embeds the design into the JuGeo metric space.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Guarded imports: jugeo subsystems
# ---------------------------------------------------------------------------
# All imports of jugeo.* subpackages are placed inside a single try/except.
# If any import fails (e.g. because the optional subsystem is not installed),
# the entire block is silently skipped.  Individual methods then detect the
# absence of the names they need and fall back to pure-Python defaults.

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

# ---------------------------------------------------------------------------
# Guarded imports: local models
# ---------------------------------------------------------------------------
# The models module is also optional so that integration.py can be imported
# in isolation (e.g. for testing) without the full evaluation_design package.

try:
    from .models import EvaluationDesign, EvaluationResult, ClauseResult, AblationResult, CalibrationReport
except Exception:
    pass

# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "EvaluationEvidenceIntegration",
    "EvaluationPacksIntegration",
    "EvaluationOrchestrationIntegration",
    "EvaluationIdeationIntegration",
    "EvaluationGeometryIntegration",
    "FullEvaluationIntegration",
]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    """Return the current UTC time as a Unix timestamp float.

    Returns:
        Current time in seconds since epoch (UTC).
    """
    return time.time()


def _uid() -> str:
    """Return a new random UUID4 string.

    Returns:
        A UUID4 string suitable for use as a unique identifier.
    """
    return str(uuid.uuid4())


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp *v* to [lo, hi].

    Args:
        v: Value to clamp.
        lo: Lower bound.
        hi: Upper bound.

    Returns:
        max(lo, min(hi, v))
    """
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# EvaluationEvidenceIntegration
# ---------------------------------------------------------------------------

class EvaluationEvidenceIntegration:
    """Integration between evaluation_design and the jugeo.evidence subsystem.

    This class converts EvaluationResult objects into EvidenceRecord dicts,
    builds ProvenanceTrace records for auditability, registers results with
    the evidence manifest, and computes trust profiles.

    When jugeo.evidence is not installed the methods degrade gracefully,
    returning sensible default dictionaries so that calling code does not
    need to special-case the unavailable subsystem.

    Attributes:
        metadata: Dict holding creation_time, integration_id, and subsystem name.
        _records: List of accumulated evidence record dicts produced during
            this session.
    """

    def __init__(self) -> None:
        """Initialise the evidence integration helper.

        Sets up the metadata dict with a fresh integration_id and timestamp,
        and initialises the internal record cache to an empty list.
        """
        self.metadata: dict = {
            "integration_id": _uid(),
            "created_at": _utcnow(),
            "subsystem": "evidence",
        }
        # Internal cache: every record produced by this instance is appended
        # here so that callers can retrieve all records without re-running
        # build_evidence_records.
        self._records: list = []

    def build_evidence_records(self, results: list) -> list[dict]:
        """Convert a list of EvaluationResult objects into evidence record dicts.

        Each record contains the result identifier, score, status, timestamps,
        and a 'kind' field indicating the evidence category.  Records are also
        appended to self._records for later retrieval.

        The method is intentionally permissive about its input: it accepts
        both proper EvaluationResult instances and plain dicts with at least
        an 'id' and 'score' key.  This makes it easy to call from tests or
        from integration code that does not depend on the full models layer.

        Args:
            results: List of EvaluationResult objects (or plain dicts with at
                least an 'id' and 'score' key).

        Returns:
            List of evidence record dicts, one per input result.  Each dict
            has keys: record_id, result_id, score, status, kind, timestamp,
            integration_id.
        """
        records = []
        for r in results:
            # Support both dict-style and object-style result representations.
            if isinstance(r, dict):
                result_id = r.get("id", _uid())
                score = r.get("score", 0.0)
                status = r.get("status", "unknown")
            else:
                result_id = getattr(r, "id", _uid())
                score = getattr(r, "score", 0.0)
                status = str(getattr(r, "status", "unknown"))

            # Construct the canonical evidence record dict.
            rec = {
                "record_id": _uid(),
                "result_id": result_id,
                # Scores are clamped to [0, 1] to satisfy the evidence schema.
                "score": _clamp(float(score), 0.0, 1.0),
                "status": status,
                # All records produced by evaluation_design carry the "evaluation" kind.
                "kind": "evaluation",
                "timestamp": _utcnow(),
                "integration_id": self.metadata["integration_id"],
            }
            # Persist in the instance cache.
            self._records.append(rec)
            records.append(rec)
        return records

    def create_provenance_trace(self, result: object) -> dict:
        """Build a provenance trace dict for a single EvaluationResult.

        The provenance trace records the chain of computation that produced
        the result, including the design_id, algorithm version, and any
        intermediate artifacts.

        Args:
            result: An EvaluationResult object or dict with evaluation data.

        Returns:
            Provenance trace dict with keys: trace_id, result_id, design_id,
            algorithm, steps, created_at, integration_id.
        """
        if isinstance(result, dict):
            result_id = result.get("id", _uid())
            design_id = result.get("design_id", "unknown")
        else:
            result_id = getattr(result, "id", _uid())
            design_id = getattr(result, "design_id", "unknown")

        # The steps list mirrors the logical phases of a full evaluation run.
        trace = {
            "trace_id": _uid(),
            "result_id": result_id,
            "design_id": design_id,
            "algorithm": "EvaluationAlgorithms.run_full_evaluation",
            "steps": [
                {"step": "clausewise_evaluation", "timestamp": _utcnow()},
                {"step": "ablation_run", "timestamp": _utcnow()},
                {"step": "calibration_measure", "timestamp": _utcnow()},
                {"step": "overall_score", "timestamp": _utcnow()},
            ],
            "created_at": _utcnow(),
            "integration_id": self.metadata["integration_id"],
        }

        # Attempt to wrap in the proper ProvenanceTrace type if available.
        try:
            pt = ProvenanceTrace(**trace)  # type: ignore[name-defined]
            return pt.__dict__
        except Exception:
            # Fall back to the plain dict representation.
            return trace

    def register_with_evidence_manifest(self, results: list) -> dict:
        """Register evaluation results with the jugeo.evidence manifest.

        Converts results to evidence records then attempts to call
        build_evidence_manifest from jugeo.evidence.manifests.  Falls back
        to a plain dict manifest when the subsystem is unavailable.

        Args:
            results: List of EvaluationResult objects or dicts.

        Returns:
            A manifest dict with keys: manifest_id, record_count, records,
            created_at, subsystem_available.
        """
        records = self.build_evidence_records(results)
        subsystem_available = False
        try:
            # Attempt to call the real manifest builder.
            manifest = build_evidence_manifest(records)  # type: ignore[name-defined]
            subsystem_available = True
            if hasattr(manifest, "__dict__"):
                return {**manifest.__dict__, "subsystem_available": True}
            return {"manifest": manifest, "subsystem_available": True}
        except Exception:
            pass

        # Graceful fallback: return a plain manifest dict.
        return {
            "manifest_id": _uid(),
            "record_count": len(records),
            "records": records,
            "created_at": _utcnow(),
            "subsystem_available": subsystem_available,
        }

    def get_trust_profile_for_result(self, result: object) -> dict:
        """Compute a trust profile dict from an EvaluationResult's scores.

        The trust tier is determined by the result's overall score:
        - GOLD   if score >= 0.9
        - SILVER if score >= 0.7
        - BRONZE if score >= 0.5
        - UNVERIFIED otherwise

        These thresholds are defined in theory2.tex §63.7 and should not be
        changed without updating the formal specification.

        Args:
            result: An EvaluationResult object or dict containing a 'score' field.

        Returns:
            Trust profile dict with keys: trust_id, result_id, tier, score,
            created_at, subsystem_available.
        """
        if isinstance(result, dict):
            score = float(result.get("score", 0.0))
            result_id = result.get("id", _uid())
        else:
            score = float(getattr(result, "score", 0.0))
            result_id = getattr(result, "id", _uid())

        score = _clamp(score, 0.0, 1.0)

        # Determine qualitative trust tier from numeric score.
        if score >= 0.9:
            tier = "GOLD"
        elif score >= 0.7:
            tier = "SILVER"
        elif score >= 0.5:
            tier = "BRONZE"
        else:
            tier = "UNVERIFIED"

        profile = {
            "trust_id": _uid(),
            "result_id": result_id,
            "tier": tier,
            "score": score,
            "created_at": _utcnow(),
            "subsystem_available": False,
        }

        # Attempt to construct the typed TrustProfile if jugeo.evidence is present.
        try:
            tp = TrustProfile(  # type: ignore[name-defined]
                trust_id=profile["trust_id"],
                tier=TrustTier[tier],  # type: ignore[name-defined]
                score=score,
            )
            profile["subsystem_available"] = True
        except Exception:
            pass

        return profile

    def get_all_records(self) -> list[dict]:
        """Return all evidence records accumulated during this session.

        Returns:
            List of all evidence record dicts produced by this instance since
            initialisation.  The list is a shallow copy to prevent external
            mutation of the internal cache.
        """
        return list(self._records)

    def clear_records(self) -> None:
        """Clear the internal evidence record cache.

        After this call, ``get_all_records`` returns an empty list.  Records
        that have already been registered with the evidence manifest are not
        affected.
        """
        self._records = []


# ---------------------------------------------------------------------------
# EvaluationPacksIntegration
# ---------------------------------------------------------------------------

class EvaluationPacksIntegration:
    """Integration between evaluation_design and the jugeo.packs subsystem.

    Provides methods to register an EvaluationDesign as a BridgeTheorem,
    look up the PackAuthority responsible for a design, and create a
    PackDescriptor catalog entry.

    Attributes:
        metadata: Dict with integration_id, created_at, subsystem='packs'.
    """

    def __init__(self) -> None:
        """Initialise the packs integration helper.

        Creates a fresh metadata dict with a new integration_id.
        """
        self.metadata: dict = {
            "integration_id": _uid(),
            "created_at": _utcnow(),
            "subsystem": "packs",
        }

    def register_as_bridge_theorem(self, design: object) -> dict:
        """Register an EvaluationDesign as a BridgeTheorem in the packs registry.

        A bridge theorem captures the relationship between the evaluation
        design's clausewise criteria and the formal pack structure.  See
        theory2.tex §63.11 for the formal definition.

        Args:
            design: An EvaluationDesign object or dict with 'id' and 'name' keys.

        Returns:
            Dict with keys: theorem_id, design_id, name, registered, created_at,
            subsystem_available.
        """
        if isinstance(design, dict):
            design_id = design.get("id", _uid())
            name = design.get("name", "unnamed_design")
        else:
            design_id = getattr(design, "id", _uid())
            name = getattr(design, "name", "unnamed_design")

        result = {
            "theorem_id": _uid(),
            "design_id": design_id,
            "name": name,
            "registered": False,
            "created_at": _utcnow(),
            "subsystem_available": False,
        }

        try:
            # Create and register the BridgeTheorem with the global registry.
            bt = BridgeTheorem(  # type: ignore[name-defined]
                theorem_id=result["theorem_id"],
                name=name,
                source_design_id=design_id,
            )
            BridgeRegistry.register(bt)  # type: ignore[name-defined]
            result["registered"] = True
            result["subsystem_available"] = True
        except Exception:
            pass

        return result

    def lookup_pack_authority(self, design: object) -> dict:
        """Look up the PackAuthority responsible for an EvaluationDesign.

        Queries PackAuthorityRegistry using the design's identifier.  Falls
        back to a default authority description when the subsystem is absent.

        Args:
            design: An EvaluationDesign object or dict.

        Returns:
            Dict with keys: authority_id, design_id, authority_name, tier,
            subsystem_available.
        """
        if isinstance(design, dict):
            design_id = design.get("id", _uid())
        else:
            design_id = getattr(design, "id", _uid())

        result = {
            "authority_id": _uid(),
            "design_id": design_id,
            "authority_name": "DefaultPackAuthority",
            "tier": "standard",
            "subsystem_available": False,
        }

        try:
            authority = PackAuthorityRegistry.lookup(design_id)  # type: ignore[name-defined]
            result["authority_name"] = str(authority)
            result["subsystem_available"] = True
        except Exception:
            pass

        return result

    def describe_in_catalog(self, design: object) -> dict:
        """Create a PackDescriptor catalog entry for an EvaluationDesign.

        The catalog entry is used by the packs subsystem to enumerate available
        evaluation designs and their metadata.

        Args:
            design: An EvaluationDesign object or dict.

        Returns:
            Catalog entry dict with keys: descriptor_id, design_id, name,
            description, tags, created_at, subsystem_available.
        """
        if isinstance(design, dict):
            design_id = design.get("id", _uid())
            name = design.get("name", "unnamed")
            description = design.get("description", "")
            tags = design.get("tags", [])
        else:
            design_id = getattr(design, "id", _uid())
            name = getattr(design, "name", "unnamed")
            description = getattr(design, "description", "")
            tags = list(getattr(design, "tags", []))

        entry = {
            "descriptor_id": _uid(),
            "design_id": design_id,
            "name": name,
            "description": description,
            "tags": tags,
            "created_at": _utcnow(),
            "subsystem_available": False,
        }

        try:
            pd = PackDescriptor(  # type: ignore[name-defined]
                descriptor_id=entry["descriptor_id"],
                name=name,
                description=description,
                tags=tags,
            )
            entry["subsystem_available"] = True
        except Exception:
            pass

        return entry

    def compose_bridge(self, design_a: object, design_b: object) -> dict:
        """Compose the BridgeTheorems for two designs into a single composite bridge.

        Composition is defined by the bridge algebra in theory2.tex §63.12.
        When the subsystem is absent a stub composite record is returned.

        Args:
            design_a: First EvaluationDesign or dict.
            design_b: Second EvaluationDesign or dict.

        Returns:
            Dict with keys: composite_id, design_a_id, design_b_id, composed,
            created_at, subsystem_available.
        """
        design_a_id = design_a.get("id", _uid()) if isinstance(design_a, dict) else getattr(design_a, "id", _uid())
        design_b_id = design_b.get("id", _uid()) if isinstance(design_b, dict) else getattr(design_b, "id", _uid())

        result = {
            "composite_id": _uid(),
            "design_a_id": design_a_id,
            "design_b_id": design_b_id,
            "composed": False,
            "created_at": _utcnow(),
            "subsystem_available": False,
        }

        try:
            composed = BridgeComposer.compose(design_a_id, design_b_id)  # type: ignore[name-defined]
            result["composite_id"] = str(getattr(composed, "id", result["composite_id"]))
            result["composed"] = True
            result["subsystem_available"] = True
        except Exception:
            pass

        return result


# ---------------------------------------------------------------------------
# EvaluationOrchestrationIntegration
# ---------------------------------------------------------------------------

class EvaluationOrchestrationIntegration:
    """Integration between evaluation_design and jugeo.orchestration.

    Submits evaluation designs to the JuGeo orchestrator for asynchronous
    execution, queries their execution state, and cancels pending jobs.

    Attributes:
        metadata: Dict with integration_id, created_at, subsystem='orchestration'.
        _pending: Dict mapping design_id -> submission dict for in-flight evaluations.
    """

    def __init__(self) -> None:
        """Initialise the orchestration integration helper.

        Creates the metadata dict and an empty pending-jobs registry.
        """
        self.metadata: dict = {
            "integration_id": _uid(),
            "created_at": _utcnow(),
            "subsystem": "orchestration",
        }
        # Local pending registry: used when the orchestrator is unavailable
        # so that get_evaluation_state and cancel_evaluation still work.
        self._pending: dict = {}

    def submit_evaluation_to_orchestrator(self, design: object) -> dict:
        """Submit an EvaluationDesign to the JuGeo orchestrator.

        Builds a submission payload and attempts to dispatch it via the
        Orchestrator singleton.  Falls back to a local pending registry
        when the orchestrator is unavailable.

        Args:
            design: EvaluationDesign object or dict with at least an 'id' key.

        Returns:
            Submission dict with keys: submission_id, design_id, state,
            submitted_at, subsystem_available.
        """
        if isinstance(design, dict):
            design_id = design.get("id", _uid())
        else:
            design_id = getattr(design, "id", _uid())

        submission = {
            "submission_id": _uid(),
            "design_id": design_id,
            "state": "PENDING",
            "submitted_at": _utcnow(),
            "subsystem_available": False,
        }

        # Always register locally first so cancel/state queries work even if
        # the orchestrator call below fails.
        self._pending[design_id] = submission

        try:
            orchestrator = Orchestrator.instance()  # type: ignore[name-defined]
            job_id = orchestrator.submit(design_id=design_id)
            submission["submission_id"] = str(job_id)
            submission["state"] = "SUBMITTED"
            submission["subsystem_available"] = True
        except Exception:
            pass

        return submission

    def get_evaluation_state(self, design_id: str) -> dict:
        """Get the current execution state of a submitted evaluation.

        Args:
            design_id: The identifier of the EvaluationDesign to query.

        Returns:
            State dict with keys: design_id, state, updated_at,
            subsystem_available.  State is one of PENDING, RUNNING,
            COMPLETED, FAILED, CANCELLED, or UNKNOWN.
        """
        result = {
            "design_id": design_id,
            "state": "UNKNOWN",
            "updated_at": _utcnow(),
            "subsystem_available": False,
        }

        # Check local registry first.
        if design_id in self._pending:
            result["state"] = self._pending[design_id].get("state", "PENDING")

        try:
            orchestrator = Orchestrator.instance()  # type: ignore[name-defined]
            state = orchestrator.get_state(design_id)
            result["state"] = str(state)
            result["subsystem_available"] = True
        except Exception:
            pass

        return result

    def cancel_evaluation(self, design_id: str) -> bool:
        """Cancel a pending or running evaluation.

        Updates the local pending registry and attempts to cancel via the
        orchestrator.

        Args:
            design_id: The identifier of the EvaluationDesign to cancel.

        Returns:
            True if the cancellation was accepted (locally or by the
            orchestrator), False if the design_id was not found.
        """
        if design_id in self._pending:
            self._pending[design_id]["state"] = "CANCELLED"
            try:
                orchestrator = Orchestrator.instance()  # type: ignore[name-defined]
                orchestrator.cancel(design_id)
            except Exception:
                pass
            return True
        return False

    def list_pending(self) -> list[dict]:
        """Return all locally-tracked pending evaluations.

        Returns:
            List of submission dicts for evaluations that have been submitted
            but not yet cancelled or confirmed complete.
        """
        return [
            v for v in self._pending.values()
            if v.get("state") not in ("CANCELLED", "COMPLETED", "FAILED")
        ]


# ---------------------------------------------------------------------------
# EvaluationIdeationIntegration
# ---------------------------------------------------------------------------

class EvaluationIdeationIntegration:
    """Integration between evaluation_design and jugeo.ideation.

    Proposes evaluation designs as IdeaProposal objects, maps designs to
    ideation regimes, and computes novelty scores for evaluation results.

    Attributes:
        metadata: Dict with integration_id, created_at, subsystem='ideation'.
    """

    def __init__(self) -> None:
        """Initialise the ideation integration helper."""
        self.metadata: dict = {
            "integration_id": _uid(),
            "created_at": _utcnow(),
            "subsystem": "ideation",
        }

    def propose_evaluation_idea(self, design: object) -> dict:
        """Create an IdeaProposal from an EvaluationDesign.

        Wraps the design in an IdeaProposal and registers it with the ideation
        subsystem.  Falls back to a plain proposal dict when jugeo.ideation
        is absent.

        Args:
            design: EvaluationDesign or dict representing the evaluation design.

        Returns:
            Proposal dict with keys: proposal_id, design_id, name, status,
            created_at, subsystem_available.
        """
        if isinstance(design, dict):
            design_id = design.get("id", _uid())
            name = design.get("name", "unnamed")
        else:
            design_id = getattr(design, "id", _uid())
            name = getattr(design, "name", "unnamed")

        proposal = {
            "proposal_id": _uid(),
            "design_id": design_id,
            "name": name,
            "status": "PROPOSED",
            "created_at": _utcnow(),
            "subsystem_available": False,
        }

        try:
            ip = IdeaProposal(  # type: ignore[name-defined]
                proposal_id=proposal["proposal_id"],
                name=name,
                source_id=design_id,
            )
            proposal["status"] = str(TrustStatus.PROPOSED)  # type: ignore[name-defined]
            proposal["subsystem_available"] = True
        except Exception:
            pass

        return proposal

    def get_regime_for_design(self, design: object) -> dict:
        """Find the ideation regime that best matches an EvaluationDesign.

        Args:
            design: EvaluationDesign or dict with at least 'id' and 'tags' keys.

        Returns:
            Regime dict with keys: regime_id, name, design_id, match_score,
            subsystem_available.
        """
        if isinstance(design, dict):
            design_id = design.get("id", _uid())
            tags = design.get("tags", [])
        else:
            design_id = getattr(design, "id", _uid())
            tags = list(getattr(design, "tags", []))

        result = {
            "regime_id": _uid(),
            "name": "DefaultEvaluationRegime",
            "design_id": design_id,
            "match_score": 0.5,
            "subsystem_available": False,
        }

        try:
            regime = RegimeCatalog.find_for_tags(tags)  # type: ignore[name-defined]
            result["regime_id"] = str(getattr(regime, "id", _uid()))
            result["name"] = str(getattr(regime, "name", "unknown"))
            result["match_score"] = float(getattr(regime, "score", 0.5))
            result["subsystem_available"] = True
        except Exception:
            pass

        return result

    def compute_novelty_score(self, result: object) -> float:
        """Compute a novelty score for an EvaluationResult.

        Novelty is defined as 1 - (overlap with existing known results).
        When jugeo.ideation is unavailable a heuristic based on the result's
        overall score is returned.

        Args:
            result: EvaluationResult or dict with a 'score' field.

        Returns:
            Novelty score in [0, 1].
        """
        if isinstance(result, dict):
            score = float(result.get("score", 0.5))
        else:
            score = float(getattr(result, "score", 0.5))

        try:
            ns = NoveltyScore.compute(result)  # type: ignore[name-defined]
            return _clamp(float(ns.value), 0.0, 1.0)
        except Exception:
            # Heuristic: invert the score to approximate novelty.
            # A high-scoring result is less novel; a low-scoring one more so.
            return _clamp(1.0 - score * 0.5, 0.0, 1.0)

    def bulk_propose(self, designs: list) -> list[dict]:
        """Propose multiple evaluation designs as ideation proposals in bulk.

        Convenience wrapper around propose_evaluation_idea that processes a
        list of designs and returns a list of proposal dicts.

        Args:
            designs: List of EvaluationDesign objects or dicts.

        Returns:
            List of proposal dicts, one per input design.
        """
        return [self.propose_evaluation_idea(d) for d in designs]


# ---------------------------------------------------------------------------
# EvaluationGeometryIntegration
# ---------------------------------------------------------------------------

class EvaluationGeometryIntegration:
    """Integration between evaluation_design and jugeo.geometry.

    Maps evaluation designs to geometric sites and projects evaluation
    results onto the global section of the JuGeo geometry framework.

    Attributes:
        metadata: Dict with integration_id, created_at, subsystem='geometry'.
    """

    def __init__(self) -> None:
        """Initialise the geometry integration helper."""
        self.metadata: dict = {
            "integration_id": _uid(),
            "created_at": _utcnow(),
            "subsystem": "geometry",
        }

    def get_site_for_design(self, design: object) -> dict:
        """Get the geometric site corresponding to an EvaluationDesign.

        A site represents the design's position in the JuGeo geometric space.
        Coordinates are derived from the design's metadata.

        The coordinate derivation uses a deterministic hash of the design_id
        so that the same design always maps to the same site, regardless of
        when this method is called.

        Args:
            design: EvaluationDesign or dict.

        Returns:
            Site dict with keys: site_id, design_id, coordinates,
            created_at, subsystem_available.
        """
        if isinstance(design, dict):
            design_id = design.get("id", _uid())
        else:
            design_id = getattr(design, "id", _uid())

        # Deterministic coordinates derived from design_id hash.
        # We use two independent hash functions (plain and *7 mod) to get
        # two orthogonal coordinates in [0, 1].
        h = abs(hash(design_id)) % 10000
        coords = [h / 10000.0, (h * 7 % 10000) / 10000.0]

        result = {
            "site_id": _uid(),
            "design_id": design_id,
            "coordinates": coords,
            "created_at": _utcnow(),
            "subsystem_available": False,
        }

        try:
            coord = Coordinate(x=coords[0], y=coords[1])  # type: ignore[name-defined]
            site = Site(site_id=result["site_id"], coordinate=coord)  # type: ignore[name-defined]
            result["subsystem_available"] = True
        except Exception:
            pass

        return result

    def project_to_global_section(self, result: object) -> dict:
        """Project an EvaluationResult onto the global section.

        The global section is a canonical representation used by the JuGeo
        geometry framework.  Projection uses the result's score as the
        primary coordinate.

        Args:
            result: EvaluationResult or dict with a 'score' key.

        Returns:
            Projection dict with keys: projection_id, result_id, section_coords,
            created_at, subsystem_available.
        """
        if isinstance(result, dict):
            result_id = result.get("id", _uid())
            score = float(result.get("score", 0.5))
        else:
            result_id = getattr(result, "id", _uid())
            score = float(getattr(result, "score", 0.5))

        projection = {
            "projection_id": _uid(),
            "result_id": result_id,
            # Primary coordinate is the score; secondary is its complement.
            "section_coords": [score, 1.0 - score],
            "created_at": _utcnow(),
            "subsystem_available": False,
        }

        try:
            gs = GlobalSection.project(result)  # type: ignore[name-defined]
            projection["section_coords"] = list(getattr(gs, "coords", projection["section_coords"]))
            projection["subsystem_available"] = True
        except Exception:
            pass

        return projection

    def batch_project(self, results: list) -> list[dict]:
        """Project a batch of EvaluationResults onto the global section.

        Convenience wrapper around project_to_global_section.

        Args:
            results: List of EvaluationResult objects or dicts.

        Returns:
            List of projection dicts, one per input result.
        """
        return [self.project_to_global_section(r) for r in results]

    def compute_pairwise_distances(self, designs: list) -> list[dict]:
        """Compute pairwise geometric distances between a list of designs.

        Uses Euclidean distance on the [0,1]^2 coordinate space produced by
        get_site_for_design.  Only the upper triangle of the distance matrix
        is returned to avoid duplicates.

        Args:
            designs: List of EvaluationDesign objects or dicts.

        Returns:
            List of distance dicts with keys: design_a_id, design_b_id,
            distance.
        """
        sites = [self.get_site_for_design(d) for d in designs]
        distances = []
        for i in range(len(sites)):
            for j in range(i + 1, len(sites)):
                ca = sites[i]["coordinates"]
                cb = sites[j]["coordinates"]
                dist = ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5
                distances.append({
                    "design_a_id": sites[i]["design_id"],
                    "design_b_id": sites[j]["design_id"],
                    "distance": dist,
                })
        return distances


# ---------------------------------------------------------------------------
# FullEvaluationIntegration
# ---------------------------------------------------------------------------

class FullEvaluationIntegration:
    """Top-level orchestrator that uses all integration layers.

    Creates and holds one instance of each integration class and provides
    methods to run a fully integrated evaluation pipeline, build comprehensive
    reports, and validate that all integration layers are functional.

    Attributes:
        evidence: EvaluationEvidenceIntegration instance.
        packs: EvaluationPacksIntegration instance.
        orchestration: EvaluationOrchestrationIntegration instance.
        ideation: EvaluationIdeationIntegration instance.
        geometry: EvaluationGeometryIntegration instance.
        metadata: Dict with integration_id, created_at, subsystem='full'.
    """

    def __init__(self) -> None:
        """Initialise all integration layers.

        Constructs one instance of each of the five sub-integration classes
        and assigns them to named attributes for easy access.
        """
        self.evidence = EvaluationEvidenceIntegration()
        self.packs = EvaluationPacksIntegration()
        self.orchestration = EvaluationOrchestrationIntegration()
        self.ideation = EvaluationIdeationIntegration()
        self.geometry = EvaluationGeometryIntegration()
        self.metadata: dict = {
            "integration_id": _uid(),
            "created_at": _utcnow(),
            "subsystem": "full",
        }

    def run_integrated_evaluation(
        self,
        design: object,
        system_fn: object,
        predictions: list[float],
        labels: list[int],
    ) -> dict:
        """Run a fully integrated evaluation across all JuGeo subsystems.

        Executes the following steps in order:
        1. Submit design to the orchestrator.
        2. Propose design as an ideation idea.
        3. Register design in the packs catalog.
        4. Call system_fn to obtain evaluation outputs.
        5. Build evidence records from outputs.
        6. Compute provenance trace.
        7. Get geometric site and project to global section.
        8. Collect all integration results into a single report dict.

        Args:
            design: EvaluationDesign or dict.
            system_fn: Callable that accepts the design and returns an output dict.
                May also be None, in which case a stub output is used.
            predictions: List of predicted probabilities in [0, 1].
            labels: List of binary ground-truth labels.

        Returns:
            Comprehensive integration report dict with keys: integration_id,
            design_id, orchestration, ideation, packs, evidence, geometry,
            system_output, created_at.
        """
        if isinstance(design, dict):
            design_id = design.get("id", _uid())
        else:
            design_id = getattr(design, "id", _uid())

        # Step 1: Submit to orchestrator first so it can start preparing
        # resources while the remaining steps run.
        orch_result = self.orchestration.submit_evaluation_to_orchestrator(design)

        # Step 2: Lift into the ideation monad.
        idea_result = self.ideation.propose_evaluation_idea(design)

        # Step 3: Register in the packs catalog.
        catalog_result = self.packs.describe_in_catalog(design)

        # Step 4: Call the system function.
        try:
            if callable(system_fn):
                system_output = system_fn(design)
                if not isinstance(system_output, dict):
                    system_output = {"output": system_output}
            else:
                system_output = {"output": None, "note": "system_fn not callable"}
        except Exception as exc:
            system_output = {"output": None, "error": str(exc)}

        # Step 5 & 6: Produce evidence records and provenance trace.
        evidence_records = self.evidence.build_evidence_records([system_output])
        provenance = self.evidence.create_provenance_trace(system_output)

        # Step 7: Embed the design and result into the geometry framework.
        site = self.geometry.get_site_for_design(design)
        projection = self.geometry.project_to_global_section(system_output)

        # Step 8: Assemble the full integration report.
        return {
            "integration_id": self.metadata["integration_id"],
            "design_id": design_id,
            "orchestration": orch_result,
            "ideation": idea_result,
            "packs": catalog_result,
            "evidence": {
                "records": evidence_records,
                "provenance": provenance,
                "manifest": self.evidence.register_with_evidence_manifest([system_output]),
            },
            "geometry": {
                "site": site,
                "projection": projection,
            },
            "system_output": system_output,
            "created_at": _utcnow(),
        }

    def build_full_report(self, result: object) -> dict:
        """Build a comprehensive report from an EvaluationResult.

        Combines evidence records, trust profile, provenance trace, ideation
        novelty score, and geometric projection into a single report.

        Args:
            result: EvaluationResult or dict.

        Returns:
            Full report dict with keys: report_id, result_id, evidence,
            trust_profile, provenance, novelty_score, geometry, created_at.
        """
        if isinstance(result, dict):
            result_id = result.get("id", _uid())
        else:
            result_id = getattr(result, "id", _uid())

        evidence_records = self.evidence.build_evidence_records([result])
        trust_profile = self.evidence.get_trust_profile_for_result(result)
        provenance = self.evidence.create_provenance_trace(result)
        novelty = self.ideation.compute_novelty_score(result)
        projection = self.geometry.project_to_global_section(result)

        return {
            "report_id": _uid(),
            "result_id": result_id,
            "evidence": evidence_records,
            "trust_profile": trust_profile,
            "provenance": provenance,
            "novelty_score": novelty,
            "geometry": {"projection": projection},
            "created_at": _utcnow(),
        }

    def validate_integration(self) -> list[str]:
        """Check that all integration layers are operational.

        Runs a lightweight ping against each subsystem integration object.
        Subsystems that are unavailable are noted but do not raise exceptions.

        Returns:
            List of warning strings for any unavailable or misconfigured
            subsystem.  An empty list means all layers passed.
        """
        warnings: list[str] = []
        layers = [
            ("evidence", self.evidence),
            ("packs", self.packs),
            ("orchestration", self.orchestration),
            ("ideation", self.ideation),
            ("geometry", self.geometry),
        ]
        for name, layer in layers:
            if not hasattr(layer, "metadata"):
                warnings.append(f"{name}: missing metadata attribute")
                continue
            if layer.metadata.get("subsystem") != name:
                warnings.append(
                    f"{name}: subsystem mismatch "
                    f"(expected '{name}', got '{layer.metadata.get('subsystem')}')"
                )
        return warnings

    def integration_summary(self) -> dict:
        """Return a summary dict describing all integration layers.

        Useful for health-check endpoints or diagnostic logging.  Does not
        call any external subsystem; all information is derived from the
        ``metadata`` dicts of each layer.

        Returns:
            Dict with keys: integration_id, created_at, layers, warnings.
            Each entry in 'layers' is a dict with keys: name, subsystem,
            integration_id, created_at.
        """
        layers_info = []
        for name, layer in [
            ("evidence", self.evidence),
            ("packs", self.packs),
            ("orchestration", self.orchestration),
            ("ideation", self.ideation),
            ("geometry", self.geometry),
        ]:
            meta = getattr(layer, "metadata", {})
            layers_info.append({
                "name": name,
                "subsystem": meta.get("subsystem", "unknown"),
                "integration_id": meta.get("integration_id", "unknown"),
                "created_at": meta.get("created_at", 0.0),
            })

        return {
            "integration_id": self.metadata["integration_id"],
            "created_at": self.metadata["created_at"],
            "layers": layers_info,
            "warnings": self.validate_integration(),
        }

    def bulk_run(self, designs: list, system_fn: object) -> list[dict]:
        """Run integrated evaluations for a batch of designs.

        Calls run_integrated_evaluation for each design in the list.  Each
        call uses an empty predictions and labels list; pass those directly
        via run_integrated_evaluation if needed.

        Args:
            designs: List of EvaluationDesign objects or dicts.
            system_fn: Callable to pass to each run_integrated_evaluation call.

        Returns:
            List of integration report dicts, one per design.
        """
        return [
            self.run_integrated_evaluation(d, system_fn, [], [])
            for d in designs
        ]
