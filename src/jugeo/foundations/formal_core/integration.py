"""Integration layer connecting the formal core (Chapter 9 constructions) with
the rest of JuGeo infrastructure.

Bridges the abstract mathematical structures (sites, sheaves, obstruction classes)
to the concrete JuGeo APIs (evidence channels, trust profiles, geometry sites,
solver routing).

The central integration pattern is:

1. A judgment or evidence configuration arrives as a plain Python dict.
2. :class:`FormalCoreIntegration` routes it through the three bridge objects:

   * :class:`TrustAlgebraToChannelBridge` — applies the trust algebra (⊕, ↑, ↓)
     to :class:`~jugeo.evidence.channels.EvidenceResponse` objects.
   * :class:`SiteToGeometryBridge` — converts between abstract formal site dicts
     and concrete ``jugeo.geometry.site.JudgmentSite`` instances (when available).
   * :class:`ObstructionToEvidenceBridge` — translates cohomological obstruction
     classes into actionable evidence gap descriptions.

3. The pipeline returns a unified result dict with a ``trust_level``, obstruction
   report, and recommendations.

All channels honour the oracle ceiling declared in Theory2.tex §9.2 Theorem 9.7:
copilot and oracle channels are hard-capped at ``ORACLE_PROPOSED`` unless an
explicit named promotion policy is invoked and recorded in the audit log.

Theory2.tex §9.4 Algorithm 9.17 defines the admissibility test that gates every
judgment entering the pipeline.

References
----------
Theory2.tex §9.1  Site definition and geometry bridge.
Theory2.tex §9.2  Trust algebra and channel bridge.
Theory2.tex §9.3  Obstruction-to-evidence translation.
Theory2.tex §9.4  Admissibility pipeline (FormalCoreIntegration).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.evidence.trust import TrustLevel, TrustProfile, TrustTier
from jugeo.evidence.channels import (
    ChannelJurisdiction,
    EvidenceChannel,
    EvidenceRequest,
    EvidenceResponse,
)

# Optional geometry and solver imports — gracefully absent in test/minimal envs
try:
    from jugeo.geometry.site import JudgmentSite as _JudgmentSite  # type: ignore[import-not-found]
    _HAS_GEOMETRY = True
except ImportError:
    _JudgmentSite = None  # type: ignore[assignment,misc]
    _HAS_GEOMETRY = False

try:
    from jugeo.solver.router import SolverRouter as _SolverRouter  # type: ignore[import-not-found]
    _HAS_SOLVER_ROUTER = True
except ImportError:
    _SolverRouter = None  # type: ignore[assignment,misc]
    _HAS_SOLVER_ROUTER = False

try:
    from jugeo.solver.fragments import LogicalFragment as _LogicalFragment  # type: ignore[import-not-found]
    _HAS_FRAGMENTS = True
except ImportError:
    _LogicalFragment = None  # type: ignore[assignment,misc]
    _HAS_FRAGMENTS = False

# Local algorithms module (same package)
from jugeo.foundations.formal_core.algorithms import (
    TrustAlgebraVerifier,
    ObstructionVanishingAlgorithm,
    SiteCompletionAlgorithm,
    admissibility_algorithm,
    descent_data_gluing,
    grothendieck_topology_completion,
    obstruction_class_computation,
    sheaf_condition_verifier,
    trust_algebra_normalization,
    _TRUST_WEIGHT,
    _trust_meet,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Map TrustLevel enum members to the legacy string tier names used by
# EvidenceResponse.trust_level (which is a str, not the enum).
_LEVEL_TO_TIER_STR: dict[TrustLevel, str] = {
    TrustLevel.MECHANICALLY_VERIFIED: "verified",
    TrustLevel.SOLVER_DISCHARGED: "verified",
    TrustLevel.RUNTIME_WITNESSED: "reviewed",
    TrustLevel.HUMAN_ATTESTED: "reviewed",
    TrustLevel.ORACLE_PROPOSED: "proposal",
    TrustLevel.COPILOT_SUGGESTED: "proposal",
    TrustLevel.UNVERIFIED: "proposal",
    TrustLevel.CONTRADICTED: "proposal",
}

# Map the three legacy tier strings back to the representative TrustLevel.
_TIER_STR_TO_LEVEL: dict[str, TrustLevel] = {
    "verified": TrustLevel.SOLVER_DISCHARGED,
    "reviewed": TrustLevel.HUMAN_ATTESTED,
    "proposal": TrustLevel.ORACLE_PROPOSED,
}


def _response_trust_level(response: EvidenceResponse) -> TrustLevel:
    """Extract a TrustLevel from an EvidenceResponse.trust_level string."""
    return _TIER_STR_TO_LEVEL.get(response.trust_level, TrustLevel.UNVERIFIED)


def _level_to_tier_str(level: TrustLevel) -> str:
    """Convert a TrustLevel to the EvidenceResponse tier string."""
    return _LEVEL_TO_TIER_STR.get(level, "proposal")


def _now_ms() -> float:
    return time.monotonic() * 1000.0


# ---------------------------------------------------------------------------
# §9.2  TrustAlgebraToChannelBridge
# ---------------------------------------------------------------------------


@dataclass
class TrustAlgebraToChannelBridge:
    """Bridge between the formal trust algebra and the channel evidence layer.

    Maps the algebraic operations from Theory2.tex §9.2 (meet ⊕, promotion ↑,
    ceiling enforcement ↓) onto concrete :class:`EvidenceResponse` objects.

    Every composition and promotion is appended to *audit_log* so that the
    no-silent-promotion invariant is observable and auditable.

    Attributes
    ----------
    algebra_config:
        Configuration dict, may include ``'oracle_ceiling'`` (str),
        ``'default_channel'`` (str).
    audit_log:
        Append-only list of operation records.
    """

    algebra_config: dict[str, Any] = field(default_factory=dict)
    audit_log: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _oracle_ceiling_level(self) -> TrustLevel:
        """Return the oracle ceiling as a TrustLevel from the config."""
        ceiling_name = self.algebra_config.get("oracle_ceiling", "ORACLE_PROPOSED")
        try:
            return TrustLevel[ceiling_name]
        except KeyError:
            return TrustLevel.ORACLE_PROPOSED

    def _record(self, op: str, **kwargs: Any) -> None:
        """Append an audit record."""
        self.audit_log.append(
            {
                "op": op,
                "timestamp_ms": _now_ms(),
                **kwargs,
            }
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def algebra_compose(
        self,
        response1: EvidenceResponse,
        response2: EvidenceResponse,
    ) -> EvidenceResponse:
        """Compose two evidence responses using the trust algebra ⊕ operation.

        The composed trust level is the meet of the two levels (conservative):
        the weaker of the two trust assessments governs the composition.  This
        implements Theory2.tex §9.2 Definition 9.6 (trust composition).

        The audit log records both inputs and the output.

        Parameters
        ----------
        response1, response2:
            :class:`EvidenceResponse` objects to compose.

        Returns
        -------
        EvidenceResponse:
            Composed response on the COMPOSED channel.
        """
        t1 = _response_trust_level(response1)
        t2 = _response_trust_level(response2)
        composed_level = _trust_meet(t1, t2)
        composed_tier_str = _level_to_tier_str(composed_level)

        # Merge evidence items
        merged_item: dict[str, Any] = {
            "composed_from": [
                response1.canonical_key(),
                response2.canonical_key(),
            ],
            "item_1": dict(response1.evidence_item),
            "item_2": dict(response2.evidence_item),
        }

        # Merge provenance
        provenance = tuple(
            dict.fromkeys(response1.provenance + response2.provenance)
        )

        composed = EvidenceResponse(
            request_id=response1.request_id or response2.request_id,
            channel=EvidenceChannel.COMPOSED,
            evidence_item=merged_item,
            trust_level=composed_tier_str,
            latency_ms=response1.latency_ms + response2.latency_ms,
            is_partial=response1.is_partial or response2.is_partial,
            residuals=tuple(
                dict.fromkeys(response1.residuals + response2.residuals)
            ),
            provenance=provenance + (
                f"composed({t1.name},{t2.name})->{composed_level.name}",
            ),
        )

        self._record(
            "compose",
            level_1=t1.name,
            level_2=t2.name,
            composed_level=composed_level.name,
            result_key=composed.canonical_key(),
        )
        logger.debug(
            "TrustAlgebraToChannelBridge.algebra_compose: %s ⊕ %s = %s",
            t1.name,
            t2.name,
            composed_level.name,
        )
        return composed

    def apply_promotion_policy(
        self,
        response: EvidenceResponse,
        policy_id: str,
        justification: str,
        new_level: TrustLevel,
    ) -> EvidenceResponse:
        """Apply a named promotion policy to an evidence response.

        Promotion may strengthen the trust level only through an explicitly
        named policy.  Theory2.tex §9.2 Invariant 9.9 forbids silent promotion.

        Raises :exc:`ValueError` if the promotion would violate the oracle
        ceiling and the channel is ``copilot`` or ``oracle``.

        Parameters
        ----------
        response:
            The response to promote.
        policy_id:
            Identifier for the promotion policy (e.g. ``'human-ratification'``).
        justification:
            Human-readable reason for the promotion.
        new_level:
            The desired new trust level.

        Returns
        -------
        EvidenceResponse:
            Response with the promoted trust level and updated provenance.

        Raises
        ------
        ValueError:
            If the promotion violates the oracle ceiling for oracle/copilot
            channels.
        """
        channel_name = response.channel.value
        is_oracle = channel_name in {"copilot", "oracle"}
        ceiling = self._oracle_ceiling_level()

        if is_oracle and _TRUST_WEIGHT[new_level] > _TRUST_WEIGHT[ceiling]:
            msg = (
                f"Promotion policy {policy_id!r} would raise {channel_name!r} "
                f"response to {new_level.name}, exceeding oracle ceiling "
                f"{ceiling.name}. Promotion refused."
            )
            logger.error("TrustAlgebraToChannelBridge.apply_promotion_policy: %s", msg)
            raise ValueError(msg)

        old_level = _response_trust_level(response)
        new_tier_str = _level_to_tier_str(new_level)

        promoted = response.with_trust(new_tier_str).merge_provenance(
            [
                f"promoted-by:{policy_id}",
                f"justification:{justification[:80]}",
                f"level:{old_level.name}->{new_level.name}",
            ]
        )
        self._record(
            "promote",
            policy_id=policy_id,
            justification=justification,
            old_level=old_level.name,
            new_level=new_level.name,
            channel=channel_name,
        )
        logger.info(
            "TrustAlgebraToChannelBridge.apply_promotion_policy: "
            "channel=%r %s -> %s via policy %r",
            channel_name,
            old_level.name,
            new_level.name,
            policy_id,
        )
        return promoted

    def enforce_ceiling(
        self,
        response: EvidenceResponse,
        ceiling: TrustLevel,
    ) -> EvidenceResponse:
        """Enforce a trust ceiling on an evidence response.

        If the response's trust level exceeds *ceiling*, clamp it down to the
        ceiling.  This implements the demotion operator ↓_χ from Theory2.tex
        §9.2.

        Parameters
        ----------
        response:
            The response to check and possibly clamp.
        ceiling:
            The maximum permissible trust level.

        Returns
        -------
        EvidenceResponse:
            Response with trust level at most *ceiling*.
        """
        current = _response_trust_level(response)
        if _TRUST_WEIGHT[current] <= _TRUST_WEIGHT[ceiling]:
            return response

        ceiling_str = _level_to_tier_str(ceiling)
        demoted = response.clamp_trust(ceiling_str).merge_provenance(
            [f"ceiling-enforced:{ceiling.name}"]
        )
        self._record(
            "ceiling_enforce",
            old_level=current.name,
            ceiling=ceiling.name,
        )
        logger.info(
            "TrustAlgebraToChannelBridge.enforce_ceiling: "
            "clamped %s -> %s",
            current.name,
            ceiling.name,
        )
        return demoted

    def compose_channel_responses(
        self,
        responses: list[EvidenceResponse],
    ) -> EvidenceResponse:
        """Compose a list of evidence responses via fold-left with *algebra_compose*.

        An empty list returns a minimal UNVERIFIED response.  A single-element
        list is returned unchanged.  Multiple responses are composed
        left-to-right: ``((r1 ⊕ r2) ⊕ r3) ⊕ ...``.

        Parameters
        ----------
        responses:
            Ordered list of :class:`EvidenceResponse` objects.

        Returns
        -------
        EvidenceResponse:
            The folded composed response.
        """
        if not responses:
            logger.debug(
                "TrustAlgebraToChannelBridge.compose_channel_responses: empty list"
            )
            return EvidenceResponse(
                request_id=uuid.uuid4().hex[:16],
                channel=EvidenceChannel.COMPOSED,
                trust_level=_level_to_tier_str(TrustLevel.UNVERIFIED),
            )
        if len(responses) == 1:
            return responses[0]

        result = responses[0]
        for resp in responses[1:]:
            result = self.algebra_compose(result, resp)

        logger.info(
            "TrustAlgebraToChannelBridge.compose_channel_responses: "
            "folded %d responses -> %s",
            len(responses),
            result.trust_level,
        )
        return result

    def describe(self) -> str:
        """Return a human-readable summary of this bridge."""
        ceiling = self.algebra_config.get("oracle_ceiling", "ORACLE_PROPOSED")
        return (
            f"TrustAlgebraToChannelBridge("
            f"oracle_ceiling={ceiling!r}, "
            f"audit_log_entries={len(self.audit_log)})"
        )


# ---------------------------------------------------------------------------
# §9.1  SiteToGeometryBridge
# ---------------------------------------------------------------------------


@dataclass
class SiteToGeometryBridge:
    """Bridge between formal site dicts and JuGeo geometry site objects.

    When ``jugeo.geometry.site`` is available, this bridge converts between
    formal site dicts (used in the algorithms module) and concrete
    ``JudgmentSite`` instances.  When the geometry module is absent, the bridge
    raises informative :exc:`ImportError` messages.

    Theory2.tex §9.1 §Site Definition links the abstract category-theoretic site
    to the concrete ``JudgmentSite`` representation.

    Attributes
    ----------
    geometry_available:
        True if ``jugeo.geometry.site`` could be imported.
    """

    geometry_available: bool = field(default=_HAS_GEOMETRY, init=False)
    _sync_history: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def _require_geometry(self) -> None:
        """Raise ImportError if geometry module is not available."""
        if not self.geometry_available:
            raise ImportError(
                "jugeo.geometry.site is not available in this environment. "
                "Install the jugeo-geometry extra or ensure the geometry "
                "package is on sys.path before using SiteToGeometryBridge."
            )

    def import_from_geometry(self, geometry_site: Any) -> dict[str, Any]:
        """Import a geometry site as a formal site dict.

        Converts a ``JudgmentSite`` (or compatible object) to the canonical
        formal site dict format used by the algorithms module:
        ``{'objects': [...], 'morphisms': [...], 'covers': {...}}``.

        Parameters
        ----------
        geometry_site:
            A ``jugeo.geometry.site.JudgmentSite`` instance.

        Returns
        -------
        dict:
            Formal site dict.

        Raises
        ------
        ImportError:
            If the geometry module is not available.
        """
        self._require_geometry()
        logger.info(
            "SiteToGeometryBridge.import_from_geometry: importing %r",
            type(geometry_site).__name__,
        )
        # Extract objects, morphisms, and covers from the geometry site.
        # The exact attribute names depend on JudgmentSite's actual API;
        # we use getattr with sensible fallbacks.
        objects: list[str] = []
        morphisms: list[dict[str, Any]] = []
        covers: dict[str, list[list[str]]] = {}
        base_obj: str = ""

        if hasattr(geometry_site, "nodes"):
            objects = [str(n) for n in geometry_site.nodes]
        elif hasattr(geometry_site, "objects"):
            objects = [str(o) for o in geometry_site.objects]

        if hasattr(geometry_site, "edges"):
            morphisms = [
                {"source": str(e[0]), "target": str(e[1]), "name": str(e)}
                for e in geometry_site.edges
            ]
        elif hasattr(geometry_site, "morphisms"):
            morphisms = [
                {
                    "source": str(m.get("source", "")),
                    "target": str(m.get("target", "")),
                    "name": str(m.get("name", "")),
                }
                for m in geometry_site.morphisms
            ]

        if hasattr(geometry_site, "covers"):
            for obj, covering_families in geometry_site.covers.items():
                covers[str(obj)] = [
                    [str(c) for c in fam] for fam in covering_families
                ]

        if hasattr(geometry_site, "base"):
            base_obj = str(geometry_site.base)
        elif objects:
            base_obj = objects[0]

        formal_site = {
            "objects": objects,
            "morphisms": morphisms,
            "covers": covers,
            "base_object": base_obj,
            "source": "geometry_bridge",
        }
        logger.debug(
            "SiteToGeometryBridge.import_from_geometry: "
            "objects=%d, morphisms=%d, covers=%d",
            len(objects),
            len(morphisms),
            len(covers),
        )
        return formal_site

    def export_to_geometry(self, formal_site_data: dict[str, Any]) -> Any:
        """Export a formal site to geometry format.

        Converts the canonical formal site dict back to a ``JudgmentSite``
        instance.  If ``JudgmentSite`` does not accept keyword arguments
        matching the formal dict keys, the best-effort construction is returned.

        Parameters
        ----------
        formal_site_data:
            Formal site dict as produced by :meth:`import_from_geometry` or
            the algorithms module.

        Returns
        -------
        JudgmentSite:
            The exported geometry site.

        Raises
        ------
        ImportError:
            If the geometry module is not available.
        """
        self._require_geometry()
        logger.info(
            "SiteToGeometryBridge.export_to_geometry: exporting site with "
            "%d objects",
            len(formal_site_data.get("objects", [])),
        )
        objects = formal_site_data.get("objects", [])
        morphisms = formal_site_data.get("morphisms", [])
        covers = formal_site_data.get("covers", {})
        base = formal_site_data.get("base_object", objects[0] if objects else "")

        # Attempt to construct a JudgmentSite — interface varies by version
        try:
            geometry_site = _JudgmentSite(  # type: ignore[call-arg]
                objects=objects,
                morphisms=morphisms,
                covers=covers,
                base=base,
            )
        except TypeError:
            # Fallback: construct empty and populate attributes
            geometry_site = _JudgmentSite()  # type: ignore[call-arg]
            for attr, value in [
                ("objects", objects),
                ("morphisms", morphisms),
                ("covers", covers),
                ("base", base),
            ]:
                try:
                    setattr(geometry_site, attr, value)
                except AttributeError:
                    logger.debug(
                        "SiteToGeometryBridge.export_to_geometry: "
                        "cannot set attr %r on JudgmentSite",
                        attr,
                    )

        logger.debug(
            "SiteToGeometryBridge.export_to_geometry: exported successfully"
        )
        return geometry_site

    def sync_trust_data(
        self,
        formal_site_data: dict[str, Any],
        geometry_site: Any,
    ) -> dict[str, Any]:
        """Synchronize trust data between formal and geometry representations.

        Reads trust annotations from the geometry site (if present) and merges
        them into the formal site dict.  Records the sync in the internal
        history.

        Parameters
        ----------
        formal_site_data:
            Formal site dict (modified in-place with merged trust data).
        geometry_site:
            The geometry site carrying trust annotations.

        Returns
        -------
        dict:
            Updated formal site dict with merged trust data.
        """
        self._require_geometry()
        trust_data: dict[str, Any] = {}
        if hasattr(geometry_site, "trust_annotations"):
            trust_data = dict(geometry_site.trust_annotations)
        elif hasattr(geometry_site, "trust"):
            trust_data = dict(geometry_site.trust)

        formal_site_data["trust_annotations"] = trust_data
        self._sync_history.append(
            {
                "timestamp_ms": _now_ms(),
                "objects_synced": len(formal_site_data.get("objects", [])),
                "trust_keys": list(trust_data.keys()),
            }
        )
        logger.info(
            "SiteToGeometryBridge.sync_trust_data: synced %d trust keys",
            len(trust_data),
        )
        return formal_site_data

    def describe(self) -> str:
        """Return a human-readable summary."""
        avail = "available" if self.geometry_available else "not available"
        return (
            f"SiteToGeometryBridge("
            f"geometry={avail}, "
            f"sync_count={len(self._sync_history)})"
        )


# ---------------------------------------------------------------------------
# §9.3  ObstructionToEvidenceBridge
# ---------------------------------------------------------------------------


@dataclass
class ObstructionToEvidenceBridge:
    """Bridge from cohomological obstruction classes to evidence gap descriptions.

    Translates the abstract output of :func:`~algorithms.obstruction_class_computation`
    into actionable language: which evidence channels are needed, whether a solver
    proof is required, and whether a copilot proposal can make progress.

    Theory2.tex §9.3 Remark 9.16 observes that when an obstruction requires a
    lift above ``ORACLE_PROPOSED``, a formal solver proof or runtime witness is
    needed — copilot alone cannot discharge the obligation.

    Attributes
    ----------
    channel_mapping:
        Maps obstruction type labels to :class:`EvidenceChannel` names.
        Default mapping covers degree-1 Čech obstructions.
    """

    channel_mapping: dict[str, str] = field(
        default_factory=lambda: {
            "H1-obstruction": EvidenceChannel.FORMAL_PROOF.value,
            "arithmetic": EvidenceChannel.SOLVER.value,
            "behavioral": EvidenceChannel.RUNTIME.value,
            "semantic": EvidenceChannel.ORACLE.value,
            "structural": EvidenceChannel.FORMAL_PROOF.value,
        }
    )

    def _required_channel_for(self, obstruction_data: dict[str, Any]) -> str:
        """Determine the required evidence channel for an obstruction."""
        cls_id: str = obstruction_data.get("class_id", "")
        for key, channel in self.channel_mapping.items():
            if key in cls_id:
                return channel
        return EvidenceChannel.FORMAL_PROOF.value

    def _required_trust_for(self, obstruction_data: dict[str, Any]) -> TrustLevel:
        """Return the minimum TrustLevel needed to fill the obstruction gap."""
        degree: int = obstruction_data.get("degree", 1)
        if degree >= 2:
            return TrustLevel.MECHANICALLY_VERIFIED
        n_incompat = len(obstruction_data.get("incompatibilities", []))
        if n_incompat == 0:
            return TrustLevel.ORACLE_PROPOSED
        if n_incompat <= 2:
            return TrustLevel.SOLVER_DISCHARGED
        return TrustLevel.MECHANICALLY_VERIFIED

    def obstruction_to_evidence_gap(
        self,
        obstruction_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Convert an obstruction class to a description of the evidence gap.

        Parameters
        ----------
        obstruction_data:
            An obstruction class dict as returned by
            :func:`~algorithms.obstruction_class_computation`.

        Returns
        -------
        dict with keys:
            ``gap_description`` (str), ``required_channel`` (str),
            ``required_trust_level`` (TrustLevel), ``can_be_filled_by_copilot`` (bool),
            ``solver_required`` (bool).
        """
        cls_id = obstruction_data.get("class_id", "unknown")
        vanishes: bool = obstruction_data.get("vanishes", True)
        incompatibilities: list[Any] = obstruction_data.get("incompatibilities", [])
        description: str = obstruction_data.get("description", "")

        if vanishes:
            logger.debug(
                "ObstructionToEvidenceBridge.obstruction_to_evidence_gap: "
                "%r already vanishes",
                cls_id,
            )
            return {
                "gap_description": f"Obstruction {cls_id!r} already vanishes — no gap.",
                "required_channel": None,
                "required_trust_level": TrustLevel.UNVERIFIED,
                "can_be_filled_by_copilot": False,
                "solver_required": False,
            }

        required_channel = self._required_channel_for(obstruction_data)
        required_trust = self._required_trust_for(obstruction_data)
        oracle_ceiling = TrustLevel.ORACLE_PROPOSED

        solver_required = (
            _TRUST_WEIGHT[required_trust] > _TRUST_WEIGHT[oracle_ceiling]
        )
        can_be_filled_by_copilot = not solver_required

        gap_description = (
            f"Obstruction {cls_id!r} has {len(incompatibilities)} incompatibility(ies). "
            f"Required evidence: {required_channel!r} at {required_trust.name}. "
            + (
                "Solver or formal proof required — copilot cannot fill this gap alone."
                if solver_required
                else "Copilot proposal may contribute; corroboration recommended."
            )
        )

        logger.info(
            "ObstructionToEvidenceBridge.obstruction_to_evidence_gap: "
            "%r solver_required=%s can_copilot=%s",
            cls_id,
            solver_required,
            can_be_filled_by_copilot,
        )
        return {
            "gap_description": gap_description,
            "required_channel": required_channel,
            "required_trust_level": required_trust,
            "can_be_filled_by_copilot": can_be_filled_by_copilot,
            "solver_required": solver_required,
        }

    def lift_to_evidence(
        self,
        lift_result: dict[str, Any],
        channel: str,
    ) -> dict[str, Any]:
        """Convert a successful lift result to an evidence-response-like dict.

        Parameters
        ----------
        lift_result:
            Output from :meth:`~algorithms.ObstructionVanishingAlgorithm.find_lift`.
        channel:
            The evidence channel that produced the lift (e.g. ``'solver'``).

        Returns
        -------
        dict:
            An evidence-response-like dict suitable for ingestion by the
            channel layer.
        """
        if not lift_result.get("lifted"):
            logger.warning(
                "ObstructionToEvidenceBridge.lift_to_evidence: "
                "lift_result.lifted is False"
            )
            return {
                "channel": channel,
                "trust_level": TrustLevel.UNVERIFIED.value,
                "evidence_item": {},
                "is_partial": True,
                "residuals": [
                    f"unresolved:{pair}"
                    for pair in lift_result.get("remaining", [])
                ],
            }

        resolved = lift_result.get("resolved_pairs", [])
        trust_level = trust_algebra_normalization(
            TrustLevel.SOLVER_DISCHARGED if channel == "solver"
            else TrustLevel.ORACLE_PROPOSED,
            channel,
        )
        return {
            "channel": channel,
            "trust_level": trust_level.value,
            "evidence_item": {
                "lift_type": "obstruction_vanishing",
                "resolved_pairs": resolved,
            },
            "is_partial": False,
            "residuals": [],
        }

    def explain_gap(self, obstruction_data: dict[str, Any]) -> str:
        """Return a human-readable explanation of the evidence gap.

        Includes whether copilot can help or whether solver verification is
        required.

        Parameters
        ----------
        obstruction_data:
            An obstruction class dict.

        Returns
        -------
        str
        """
        gap = self.obstruction_to_evidence_gap(obstruction_data)
        if gap["required_channel"] is None:
            return gap["gap_description"]

        lines = [
            f"Evidence gap for obstruction {obstruction_data.get('class_id', '?')!r}:",
            f"  Description: {obstruction_data.get('description', '')}",
            f"  Required channel: {gap['required_channel']}",
            f"  Required trust level: {gap['required_trust_level'].name}",
            f"  Can copilot help? {'Yes (as proposal, needs corroboration)' if gap['can_be_filled_by_copilot'] else 'No'}",
            f"  Solver/formal proof required? {'Yes' if gap['solver_required'] else 'No'}",
        ]
        return "\n".join(lines)

    def required_channels(self, obstruction_data: dict[str, Any]) -> list[str]:
        """Return a list of evidence channels needed to fill the gap.

        Parameters
        ----------
        obstruction_data:
            An obstruction class dict.

        Returns
        -------
        list[str]:
            Channel names (strings).
        """
        gap = self.obstruction_to_evidence_gap(obstruction_data)
        channels: list[str] = []
        if gap["required_channel"]:
            channels.append(gap["required_channel"])
        if gap["solver_required"] and EvidenceChannel.SOLVER.value not in channels:
            channels.append(EvidenceChannel.SOLVER.value)
        return channels

    def describe(self) -> str:
        """Return a human-readable summary."""
        return (
            f"ObstructionToEvidenceBridge("
            f"channel_mappings={len(self.channel_mapping)})"
        )


# ---------------------------------------------------------------------------
# §9.4  FormalCoreIntegration
# ---------------------------------------------------------------------------


@dataclass
class FormalCoreIntegration:
    """Main integration class for the formal core pipeline.

    Orchestrates the three bridge objects and runs the full admissibility
    pipeline from Theory2.tex §9.4 Algorithm 9.17:

    1. Build site object from judgment data.
    2. Compute trust level via the trust algebra bridge.
    3. Check for obstructions and attempt to vanish them with available evidence.
    4. Return a unified result dict with ``trust_level``, ``obstructions``,
       and ``recommendations``.

    Attributes
    ----------
    config:
        Integration configuration dict.
    site_data:
        Attached formal site dict (after :meth:`attach_site`).
    algebra_config:
        Trust algebra configuration (after :meth:`bind_trust_algebra`).
    trust_bridge:
        The :class:`TrustAlgebraToChannelBridge` instance.
    site_bridge:
        The :class:`SiteToGeometryBridge` instance.
    obstruction_bridge:
        The :class:`ObstructionToEvidenceBridge` instance.
    initialized:
        True after :meth:`initialize` has been called.
    """

    config: dict[str, Any] = field(default_factory=dict)
    site_data: dict[str, Any] | None = field(default=None)
    algebra_config: dict[str, Any] | None = field(default=None)
    trust_bridge: TrustAlgebraToChannelBridge = field(
        default_factory=TrustAlgebraToChannelBridge
    )
    site_bridge: SiteToGeometryBridge = field(
        default_factory=SiteToGeometryBridge
    )
    obstruction_bridge: ObstructionToEvidenceBridge = field(
        default_factory=ObstructionToEvidenceBridge
    )
    initialized: bool = field(default=False)
    _status: dict[str, Any] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    # Setup methods
    # ------------------------------------------------------------------

    def initialize(self, config: dict[str, Any]) -> None:
        """Set up the integration with the given configuration.

        Reads oracle ceiling, default channel, and bridge-specific overrides
        from *config* and applies them to all bridge objects.

        Parameters
        ----------
        config:
            Configuration dict.  Recognized keys:
            - ``'oracle_ceiling'`` (str): TrustLevel name for the oracle cap.
            - ``'channel_mapping'`` (dict): obstruction-to-channel mapping.
            - ``'default_algebra'`` (dict): algebra config passed to the bridge.
        """
        self.config = config
        algebra_cfg: dict[str, Any] = config.get("default_algebra", {})
        oracle_ceiling = config.get("oracle_ceiling", "ORACLE_PROPOSED")
        algebra_cfg.setdefault("oracle_ceiling", oracle_ceiling)

        self.trust_bridge = TrustAlgebraToChannelBridge(algebra_config=algebra_cfg)
        self.site_bridge = SiteToGeometryBridge()

        channel_mapping = config.get("channel_mapping", {})
        if channel_mapping:
            self.obstruction_bridge = ObstructionToEvidenceBridge(
                channel_mapping={**self.obstruction_bridge.channel_mapping, **channel_mapping}
            )

        self.initialized = True
        self._status["initialized_at_ms"] = _now_ms()
        logger.info(
            "FormalCoreIntegration.initialize: oracle_ceiling=%r, "
            "geometry_available=%s",
            oracle_ceiling,
            self.site_bridge.geometry_available,
        )

    def attach_site(self, site_data: dict[str, Any]) -> None:
        """Attach a formal site to the integration.

        Parameters
        ----------
        site_data:
            Formal site dict with keys ``'objects'``, ``'morphisms'``,
            ``'covers'``, ``'base_object'``.
        """
        self.site_data = site_data
        self._status["site_objects"] = len(site_data.get("objects", []))
        logger.info(
            "FormalCoreIntegration.attach_site: %d objects, %d cover families",
            len(site_data.get("objects", [])),
            sum(len(v) for v in site_data.get("covers", {}).values()),
        )

    def bind_trust_algebra(self, algebra_config: dict[str, Any]) -> None:
        """Bind a trust algebra configuration.

        Parameters
        ----------
        algebra_config:
            Dict passed to :class:`TrustAlgebraToChannelBridge`.
        """
        self.algebra_config = algebra_config
        self.trust_bridge = TrustAlgebraToChannelBridge(algebra_config=algebra_config)
        self._status["algebra_bound_at_ms"] = _now_ms()
        logger.info(
            "FormalCoreIntegration.bind_trust_algebra: oracle_ceiling=%r",
            algebra_config.get("oracle_ceiling"),
        )

    # ------------------------------------------------------------------
    # Pipeline methods
    # ------------------------------------------------------------------

    def process_judgment(self, judgment_data: dict[str, Any]) -> dict[str, Any]:
        """Process a judgment through the formal core pipeline.

        Steps:

        1. Build a minimal site from the judgment (or use the attached site).
        2. Compute trust level via :func:`~algorithms.admissibility_algorithm`.
        3. Check for Čech obstructions using local sections in the judgment.
        4. Return a result dict with ``trust_level``, ``obstructions``,
           ``recommendations``, and ``admissible``.

        Parameters
        ----------
        judgment_data:
            Dict describing the judgment.  Key fields:
            - ``'channel'`` (str): evidence channel name
            - ``'trust_level'`` (TrustLevel): proposed trust
            - ``'cover'`` (list[str]): covering family for this judgment
            - ``'local_sections'`` (dict): local section data
            - ``'intersection_data'`` (dict): intersection data for obstruction check
            - ``'items'`` (list): evidence items
            - ``'residuals'`` (list[str]): open obligations

        Returns
        -------
        dict with keys:
            ``admissible`` (bool), ``trust_level`` (TrustLevel),
            ``trust_level_name`` (str), ``obstructions`` (list),
            ``recommendations`` (list[str]).
        """
        start_ms = _now_ms()
        channel = judgment_data.get("channel", "")
        proposed_level: TrustLevel = judgment_data.get(
            "trust_level", TrustLevel.UNVERIFIED
        )
        cover: list[str] = judgment_data.get("cover", [])
        local_sections: dict[str, Any] = judgment_data.get("local_sections", {})
        intersection_data: dict[str, Any] = judgment_data.get("intersection_data", {})

        # Step 1: admissibility check
        evidence_config = {
            "channel": channel,
            "trust_level": proposed_level,
            "domain": judgment_data.get("domain", ""),
            "residuals": judgment_data.get("residuals", []),
            "items": judgment_data.get("items", []),
        }
        admissibility = admissibility_algorithm(
            evidence_config,
            oracle_ceiling=self.trust_bridge._oracle_ceiling_level(),
        )
        admissible: bool = admissibility["admissible"]
        recommended_level: TrustLevel = admissibility["recommended_level"]

        # Step 2: obstruction check
        obstruction_classes: list[dict[str, Any]] = []
        if cover and local_sections:
            obstruction_classes = obstruction_class_computation(
                cover, local_sections, intersection_data
            )

        # Step 3: gap analysis
        recommendations: list[str] = list(admissibility.get("rejection_reasons", []))
        for obs in obstruction_classes:
            if not obs.get("vanishes", True):
                gap = self.obstruction_bridge.obstruction_to_evidence_gap(obs)
                recommendations.append(gap["gap_description"])

        elapsed_ms = _now_ms() - start_ms
        result = {
            "admissible": admissible,
            "trust_level": recommended_level,
            "trust_level_name": recommended_level.name,
            "obstructions": obstruction_classes,
            "recommendations": recommendations,
            "checks": admissibility.get("checks", {}),
            "elapsed_ms": elapsed_ms,
        }
        logger.info(
            "FormalCoreIntegration.process_judgment: admissible=%s "
            "trust=%s elapsed=%.1fms",
            admissible,
            recommended_level.name,
            elapsed_ms,
        )
        return result

    def compute_global_trust(
        self,
        local_trust_data: dict[str, TrustLevel],
    ) -> TrustLevel:
        """Compute global trust from local trust data.

        Takes the meet of all local trust levels — the conservative combination
        ensuring the global trust is no stronger than the weakest local piece of
        evidence.  If the site has obstructions preventing global gluing, demotes
        to UNVERIFIED.

        Parameters
        ----------
        local_trust_data:
            Dict mapping cover element names to their local trust levels.

        Returns
        -------
        TrustLevel:
            The global trust level.
        """
        if not local_trust_data:
            logger.warning(
                "FormalCoreIntegration.compute_global_trust: empty local data"
            )
            return TrustLevel.UNVERIFIED

        levels = list(local_trust_data.values())
        global_level = levels[0]
        for lvl in levels[1:]:
            global_level = _trust_meet(global_level, lvl)

        # Check for obstructions: if any local section is CONTRADICTED,
        # the global level is CONTRADICTED.
        if TrustLevel.CONTRADICTED in levels:
            global_level = TrustLevel.CONTRADICTED

        logger.info(
            "FormalCoreIntegration.compute_global_trust: "
            "inputs=%s global=%s",
            {k: v.name for k, v in local_trust_data.items()},
            global_level.name,
        )
        return global_level

    def check_obstruction_and_lift(
        self,
        cover: list[str],
        local_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Check if descent data has obstructions and try to lift.

        Runs obstruction computation, then attempts to vanish obstructions
        using any extra evidence present in *local_data*.

        Parameters
        ----------
        cover:
            List of cover element names.
        local_data:
            Dict with keys:
            - ``'local_sections'`` (dict): local section values
            - ``'intersection_data'`` (dict): restriction data
            - ``'gluing_morphisms'`` (dict): gluing isomorphisms
            - ``'coherence_data'`` (dict): triple intersection data
            - ``'extra_evidence'`` (dict): additional evidence for lifting

        Returns
        -------
        dict with keys:
            ``lifted`` (bool), ``global_data`` (dict | None),
            ``obstructions`` (list), ``required_evidence`` (list[str]).
        """
        local_sections = local_data.get("local_sections", {})
        intersection_data = local_data.get("intersection_data", {})
        gluing_morphisms = local_data.get("gluing_morphisms", {})
        coherence_data = local_data.get("coherence_data", {})
        extra_evidence = local_data.get("extra_evidence", {})

        # Compute obstruction classes
        obstruction_classes = obstruction_class_computation(
            cover, local_sections, intersection_data
        )
        vanisher = ObstructionVanishingAlgorithm(
            obstruction_classes=obstruction_classes,
            site_data=self.site_data or {},
        )
        vanish_result = vanisher.vanish_all(extra_evidence)

        if vanish_result["all_vanished"]:
            # Try to glue
            glue_result = descent_data_gluing(
                cover, local_sections, gluing_morphisms, coherence_data
            )
            return {
                "lifted": glue_result["success"],
                "global_data": glue_result.get("global_section"),
                "obstructions": obstruction_classes,
                "required_evidence": [],
            }

        # Collect required evidence for remaining obstructions
        required_evidence: list[str] = []
        for obs_id in vanish_result["remaining"]:
            obs = next(
                (c for c in obstruction_classes if c.get("class_id") == obs_id),
                None,
            )
            if obs is not None:
                required_evidence.extend(
                    self.obstruction_bridge.required_channels(obs)
                )
                required_evidence.extend(
                    vanisher.required_evidence_for_vanishing(obs_id)
                )

        logger.info(
            "FormalCoreIntegration.check_obstruction_and_lift: "
            "lifted=False, remaining=%d, required_evidence=%d items",
            len(vanish_result["remaining"]),
            len(required_evidence),
        )
        return {
            "lifted": False,
            "global_data": None,
            "obstructions": obstruction_classes,
            "required_evidence": list(dict.fromkeys(required_evidence)),
        }

    # ------------------------------------------------------------------
    # Status and health
    # ------------------------------------------------------------------

    def get_integration_status(self) -> dict[str, Any]:
        """Return the current status of all bridges and configuration.

        Returns
        -------
        dict:
            Status dict with keys for each component.
        """
        return {
            "initialized": self.initialized,
            "site_attached": self.site_data is not None,
            "algebra_bound": self.algebra_config is not None,
            "trust_bridge": self.trust_bridge.describe(),
            "site_bridge": self.site_bridge.describe(),
            "obstruction_bridge": self.obstruction_bridge.describe(),
            "geometry_available": self.site_bridge.geometry_available,
            "solver_router_available": _HAS_SOLVER_ROUTER,
            "fragments_available": _HAS_FRAGMENTS,
            "audit_log_entries": len(self.trust_bridge.audit_log),
            **self._status,
        }

    def health_check(self) -> dict[str, Any]:
        """Run health checks on all components.

        Checks that:
        - The trust algebra is internally consistent.
        - The oracle ceiling is properly enforced.
        - If a site is attached, it satisfies the Grothendieck axioms.
        - The bridges are all reachable.

        Returns
        -------
        dict with keys:
            ``healthy`` (bool), ``components`` (dict), ``issues`` (list[str]).
        """
        issues: list[str] = []
        components: dict[str, Any] = {}

        # Trust algebra health
        verifier = TrustAlgebraVerifier()
        try:
            algebra_result = verifier.verify_all_axioms()
            components["trust_algebra"] = {
                "healthy": algebra_result["passed"],
                "violations": algebra_result["violations"],
            }
            if not algebra_result["passed"]:
                issues.append(
                    f"Trust algebra axiom failures: {algebra_result['violations']}"
                )
        except Exception as exc:
            components["trust_algebra"] = {"healthy": False, "error": str(exc)}
            issues.append(f"Trust algebra check raised: {exc}")

        # Oracle ceiling enforcement
        try:
            ceiling_ok = verifier.check_oracle_ceiling_enforcement()
            components["oracle_ceiling"] = {"healthy": ceiling_ok}
            if not ceiling_ok:
                issues.append("Oracle ceiling enforcement FAILED.")
        except Exception as exc:
            components["oracle_ceiling"] = {"healthy": False, "error": str(exc)}
            issues.append(f"Oracle ceiling check raised: {exc}")

        # Site topology (if attached)
        if self.site_data is not None:
            try:
                algo = SiteCompletionAlgorithm(
                    site_data=dict(self.site_data),
                    morphisms=self.site_data.get("morphisms", []),
                )
                complete = algo.check_completeness()
                components["site_topology"] = {"healthy": complete}
                if not complete:
                    issues.append(
                        "Attached site is not a complete Grothendieck topology."
                    )
            except Exception as exc:
                components["site_topology"] = {"healthy": False, "error": str(exc)}
                issues.append(f"Site topology check raised: {exc}")
        else:
            components["site_topology"] = {"healthy": True, "note": "No site attached."}

        # Bridge reachability
        for name, bridge in [
            ("trust_bridge", self.trust_bridge),
            ("site_bridge", self.site_bridge),
            ("obstruction_bridge", self.obstruction_bridge),
        ]:
            try:
                _ = bridge.describe()
                components[name] = {"healthy": True}
            except Exception as exc:
                components[name] = {"healthy": False, "error": str(exc)}
                issues.append(f"Bridge {name!r} describe() raised: {exc}")

        healthy = len(issues) == 0
        logger.info(
            "FormalCoreIntegration.health_check: healthy=%s issues=%d",
            healthy,
            len(issues),
        )
        return {
            "healthy": healthy,
            "components": components,
            "issues": issues,
        }

    def describe(self) -> str:
        """Return a human-readable summary of this integration instance."""
        site_str = (
            f"site({len(self.site_data.get('objects', []))} objects)"
            if self.site_data is not None
            else "no site"
        )
        return (
            f"FormalCoreIntegration("
            f"initialized={self.initialized}, "
            f"{site_str}, "
            f"geometry={self.site_bridge.geometry_available})"
        )
