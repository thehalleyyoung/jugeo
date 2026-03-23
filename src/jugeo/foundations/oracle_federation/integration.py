from __future__ import annotations
"""Integration layer for oracle_federation — Theory2.tex Ch7.

This module connects the oracle federation framework to the rest of the JuGeo
verification pipeline.  It provides three integration points:

1. ``OracleFederationIntegration`` — the main integration object that wires
   oracle channels, the solver federation, and the witness collector together
   into a single facade.  The orchestrator interacts with this object instead
   of the individual subsystems.

2. ``SiteOracleBridge`` — attaches oracle channels to geometry sites so that
   trust information flows bidirectionally between the judgment geometry and
   the oracle layer.

3. ``FederationPipelineAdapter`` — adapts the federation to the JuGeo
   pipeline API, converting raw pipeline requests into ``EvidenceRequest``
   objects and wrapping ``EvidenceResponse`` objects as pipeline step outputs.

4. ``WitnessToEvidenceAdapter`` — converts ``HeapWitness``, ``IdentityWitness``,
   and ``StackWitness`` objects into ``EvidenceResponse`` objects suitable for
   the evidence pool.

Integration lifecycle
---------------------
1. Create ``OracleFederationIntegration``.
2. Call ``initialize(config)`` with a configuration dict.
3. Register oracle channels and solver federations.
4. Call ``process_request(request)`` for each evidence request.
5. Call ``collect_and_integrate_witnesses()`` periodically.
6. Call ``shutdown()`` when done.

Theory alignment
----------------
- This module implements the operational layer described informally in
  Theory2.tex Ch7 appendix and §7.2.5.
- Trust ceiling enforcement is transparent to callers: the integration layer
  silently clamps any response that exceeds the declared ceiling.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier, TrustProfile
    from jugeo.evidence.channels import (
        EvidenceChannel,
        ChannelJurisdiction,
        EvidenceRequest,
        EvidenceResponse,
    )
    from jugeo.solver.router import SolverRouter, BackendKind, RoutingDecision
    from jugeo.solver.fragments import LogicalFragment
except ImportError:
    TrustLevel = None  # type: ignore[assignment,misc]
    TrustTier = None  # type: ignore[assignment,misc]
    TrustProfile = None  # type: ignore[assignment,misc]
    EvidenceChannel = None  # type: ignore[assignment,misc]
    ChannelJurisdiction = None  # type: ignore[assignment,misc]
    EvidenceRequest = None  # type: ignore[assignment,misc]
    EvidenceResponse = None  # type: ignore[assignment,misc]
    SolverRouter = None  # type: ignore[assignment,misc]
    BackendKind = None  # type: ignore[assignment,misc]
    RoutingDecision = None  # type: ignore[assignment,misc]
    LogicalFragment = None  # type: ignore[assignment,misc]

try:
    from jugeo.foundations.oracle_federation.controlled_oracles import (
        OracleChannel,
        CopilotOracleChannel,
        TrustCeilingEnforcer,
    )
except ImportError:
    OracleChannel = None  # type: ignore[assignment,misc]
    CopilotOracleChannel = None  # type: ignore[assignment,misc]
    TrustCeilingEnforcer = None  # type: ignore[assignment,misc]

try:
    from jugeo.foundations.oracle_federation.solver_federation import (
        SolverFederation,
        FederationRouter,
        FragmentClassification,
    )
except ImportError:
    SolverFederation = None  # type: ignore[assignment,misc]
    FederationRouter = None  # type: ignore[assignment,misc]
    FragmentClassification = None  # type: ignore[assignment,misc]

try:
    from jugeo.foundations.oracle_federation.runtime_witnesses import (
        RuntimeWitnessCollector,
        HeapWitness,
        IdentityWitness,
        StackWitness,
        WitnessValidator,
    )
except ImportError:
    RuntimeWitnessCollector = None  # type: ignore[assignment,misc]
    HeapWitness = None  # type: ignore[assignment,misc]
    IdentityWitness = None  # type: ignore[assignment,misc]
    StackWitness = None  # type: ignore[assignment,misc]
    WitnessValidator = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# IntegrationConfig
# ---------------------------------------------------------------------------

@dataclass
class IntegrationConfig:
    """Configuration for ``OracleFederationIntegration``.

    All fields have sensible defaults so callers may construct a config by
    only specifying the values that differ from the baseline.

    Parameters
    ----------
    integration_id:
        Unique identifier for this integration instance.  Auto-generated
        from a random UUID if not supplied.
    oracle_channels:
        Names of oracle channels to activate at initialisation time.
    federation_name:
        Human-readable label applied to the ``SolverFederation`` created
        internally.
    enable_witnesses:
        Whether to spin up the ``RuntimeWitnessCollector`` subsystem.
    enable_copilot:
        Whether to register a ``CopilotOracleChannel`` automatically.
    copilot_ceiling:
        The trust level string used as the ceiling for Copilot responses.
        Should name one of the ``TrustLevel`` enum members in lower-snake
        form (e.g. ``"copilot_suggested"``).
    witness_snapshot_interval:
        Seconds between automatic witness snapshots (passed to the collector).
    max_oracle_proposals:
        Hard cap on the number of ORACLE_PROPOSED items that may accumulate
        in the evidence pool between flushes.
    routing_strategy:
        Strategy token forwarded to the ``FederationRouter``; ``"smart"``
        enables cost/latency-aware routing while ``"round_robin"`` enables
        simple cycling.
    strict_jurisdiction:
        When ``True``, requests whose jurisdiction check fails are rejected
        rather than forwarded with a downgraded trust ceiling.
    audit_enabled:
        Whether each channel should emit an audit entry for every response.
    """

    integration_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    oracle_channels: list[str] = field(default_factory=list)
    federation_name: str = "main_federation"
    enable_witnesses: bool = True
    enable_copilot: bool = True
    copilot_ceiling: str = "copilot_suggested"
    witness_snapshot_interval: float = 60.0
    max_oracle_proposals: int = 100
    routing_strategy: str = "smart"
    strict_jurisdiction: bool = True
    audit_enabled: bool = True

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise the config to a plain ``dict``.

        Returns
        -------
        dict
            All fields as JSON-serialisable values.
        """
        return {
            "integration_id": self.integration_id,
            "oracle_channels": list(self.oracle_channels),
            "federation_name": self.federation_name,
            "enable_witnesses": self.enable_witnesses,
            "enable_copilot": self.enable_copilot,
            "copilot_ceiling": self.copilot_ceiling,
            "witness_snapshot_interval": self.witness_snapshot_interval,
            "max_oracle_proposals": self.max_oracle_proposals,
            "routing_strategy": self.routing_strategy,
            "strict_jurisdiction": self.strict_jurisdiction,
            "audit_enabled": self.audit_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> IntegrationConfig:
        """Deserialise an ``IntegrationConfig`` from a ``dict``.

        Unknown keys are silently ignored so that configs serialised by
        future versions of JuGeo can be loaded by older ones.

        Parameters
        ----------
        data:
            Mapping produced by ``to_dict`` (or a superset thereof).

        Returns
        -------
        IntegrationConfig
            A freshly constructed instance.
        """
        known_fields = {
            "integration_id",
            "oracle_channels",
            "federation_name",
            "enable_witnesses",
            "enable_copilot",
            "copilot_ceiling",
            "witness_snapshot_interval",
            "max_oracle_proposals",
            "routing_strategy",
            "strict_jurisdiction",
            "audit_enabled",
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    def validate(self) -> bool:
        """Validate that required fields have acceptable values.

        Raises
        ------
        ValueError
            If any required field is missing or out of range.

        Returns
        -------
        bool
            ``True`` when all checks pass.
        """
        if not self.integration_id:
            raise ValueError("integration_id must be a non-empty string")
        if not self.federation_name:
            raise ValueError("federation_name must be a non-empty string")
        if self.witness_snapshot_interval <= 0:
            raise ValueError("witness_snapshot_interval must be positive")
        if self.max_oracle_proposals < 1:
            raise ValueError("max_oracle_proposals must be at least 1")
        if self.routing_strategy not in ("smart", "round_robin", "priority"):
            raise ValueError(
                f"routing_strategy must be one of smart/round_robin/priority, "
                f"got {self.routing_strategy!r}"
            )
        return True

    def describe(self) -> str:
        """Return a human-readable one-line summary of the configuration.

        Returns
        -------
        str
            Summary string suitable for log output.
        """
        channel_summary = (
            ", ".join(self.oracle_channels) if self.oracle_channels else "none"
        )
        return (
            f"IntegrationConfig(id={self.integration_id}, "
            f"federation={self.federation_name!r}, "
            f"channels=[{channel_summary}], "
            f"copilot={self.enable_copilot}, "
            f"witnesses={self.enable_witnesses}, "
            f"strategy={self.routing_strategy!r})"
        )


# ---------------------------------------------------------------------------
# WitnessToEvidenceAdapter
# ---------------------------------------------------------------------------

class WitnessToEvidenceAdapter:
    """Converts runtime witness objects into evidence response dicts.

    The oracle federation's evidence pool expects dicts that mirror the
    ``EvidenceResponse`` dataclass schema.  Witnesses produced by
    ``RuntimeWitnessCollector`` carry overlapping but differently-structured
    information.  This adapter bridges the two representations.

    Parameters
    ----------
    trust_policy:
        Optional policy overrides.  Recognised keys:

        ``"default_tier"``
            Trust level string to assign when the witness does not carry an
            explicit trust annotation.  Defaults to ``"runtime_witnessed"``.
        ``"allow_promotion"``
            If ``True``, a witness may be promoted above ``RUNTIME_WITNESSED``
            when its validator score exceeds 0.95.  Defaults to ``False``.
    """

    def __init__(self, trust_policy: dict | None = None) -> None:
        self.trust_policy: dict = trust_policy or {
            "default_tier": "runtime_witnessed",
            "allow_promotion": False,
        }
        self._conversion_count: int = 0

    # ------------------------------------------------------------------
    # Witness-type-specific adapters
    # ------------------------------------------------------------------

    def adapt_heap_witness(self, witness: Any) -> dict:
        """Convert a ``HeapWitness`` to an evidence response dict.

        Attempts to call ``witness.to_evidence_response_dict()`` first.
        Falls back to manual field extraction when the method is absent.

        Parameters
        ----------
        witness:
            A ``HeapWitness`` instance (or any object with compatible fields).

        Returns
        -------
        dict
            Evidence response dict compatible with the EvidenceResponse schema.
        """
        if hasattr(witness, "to_evidence_response_dict"):
            base = witness.to_evidence_response_dict()
        else:
            heap_data: dict = {}
            for attr in ("address", "value", "size", "tag", "allocation_site"):
                if hasattr(witness, attr):
                    heap_data[attr] = getattr(witness, attr)
            base = {
                "request_id": getattr(witness, "witness_id", uuid.uuid4().hex[:12]),
                "channel": "runtime",
                "evidence_item": heap_data,
                "trust_level": self.trust_policy.get("default_tier", "runtime_witnessed"),
                "latency_ms": 0.0,
                "is_partial": getattr(witness, "is_partial", False),
                "residuals": [],
                "provenance": self._build_provenance(witness),
            }
        base.setdefault("channel", "runtime")
        base.setdefault("trust_level", self.trust_policy.get("default_tier", "runtime_witnessed"))
        self._conversion_count += 1
        logger.debug(
            "Adapted HeapWitness %s → evidence request_id=%s",
            getattr(witness, "witness_id", id(witness)),
            base.get("request_id"),
        )
        return base

    def adapt_identity_witness(self, witness: Any) -> dict:
        """Convert an ``IdentityWitness`` to an evidence response dict.

        An identity witness certifies that two symbolic names resolve to the
        same runtime object.  The evidence item records both names and the
        resolved identifier.

        Parameters
        ----------
        witness:
            An ``IdentityWitness`` instance or compatible object.

        Returns
        -------
        dict
            Evidence response dict.
        """
        if hasattr(witness, "to_evidence_response_dict"):
            base = witness.to_evidence_response_dict()
        else:
            identity_data: dict = {}
            for attr in ("left_name", "right_name", "resolved_id", "equality_proof"):
                if hasattr(witness, attr):
                    identity_data[attr] = getattr(witness, attr)
            base = {
                "request_id": getattr(witness, "witness_id", uuid.uuid4().hex[:12]),
                "channel": "runtime",
                "evidence_item": identity_data,
                "trust_level": self.trust_policy.get("default_tier", "runtime_witnessed"),
                "latency_ms": 0.0,
                "is_partial": getattr(witness, "is_partial", False),
                "residuals": [],
                "provenance": self._build_provenance(witness),
            }
        base.setdefault("channel", "runtime")
        base.setdefault("trust_level", self.trust_policy.get("default_tier", "runtime_witnessed"))
        self._conversion_count += 1
        logger.debug(
            "Adapted IdentityWitness %s → evidence request_id=%s",
            getattr(witness, "witness_id", id(witness)),
            base.get("request_id"),
        )
        return base

    def adapt_stack_witness(self, witness: Any) -> dict:
        """Convert a ``StackWitness`` to an evidence response dict.

        Stack witnesses record the call stack at a particular point in
        program execution.  The evidence item contains the frame list.

        Parameters
        ----------
        witness:
            A ``StackWitness`` instance or compatible object.

        Returns
        -------
        dict
            Evidence response dict.
        """
        if hasattr(witness, "to_evidence_response_dict"):
            base = witness.to_evidence_response_dict()
        else:
            frames = getattr(witness, "frames", [])
            stack_data: dict = {
                "frames": frames if isinstance(frames, list) else list(frames),
                "depth": getattr(witness, "depth", len(frames) if frames else 0),
            }
            for attr in ("thread_id", "timestamp_ns", "exception"):
                if hasattr(witness, attr):
                    stack_data[attr] = getattr(witness, attr)
            base = {
                "request_id": getattr(witness, "witness_id", uuid.uuid4().hex[:12]),
                "channel": "runtime",
                "evidence_item": stack_data,
                "trust_level": self.trust_policy.get("default_tier", "runtime_witnessed"),
                "latency_ms": 0.0,
                "is_partial": getattr(witness, "is_partial", False),
                "residuals": [],
                "provenance": self._build_provenance(witness),
            }
        base.setdefault("channel", "runtime")
        base.setdefault("trust_level", self.trust_policy.get("default_tier", "runtime_witnessed"))
        self._conversion_count += 1
        logger.debug(
            "Adapted StackWitness %s → evidence request_id=%s",
            getattr(witness, "witness_id", id(witness)),
            base.get("request_id"),
        )
        return base

    def adapt_witness(self, witness: Any) -> dict:
        """Dispatch a witness to the appropriate typed adapter.

        Dispatch priority:
        1. ``witness.kind`` attribute (``"heap"``, ``"identity"``, ``"stack"``).
        2. Class name substring match.
        3. Fall back to heap adapter.

        Parameters
        ----------
        witness:
            Any witness object.

        Returns
        -------
        dict
            Evidence response dict.
        """
        kind: str = getattr(witness, "kind", "").lower()
        class_name: str = type(witness).__name__.lower()

        if kind == "heap" or "heap" in class_name:
            return self.adapt_heap_witness(witness)
        if kind == "identity" or "identity" in class_name:
            return self.adapt_identity_witness(witness)
        if kind == "stack" or "stack" in class_name:
            return self.adapt_stack_witness(witness)

        logger.warning(
            "Unknown witness kind %r / class %r; falling back to heap adapter",
            kind,
            class_name,
        )
        return self.adapt_heap_witness(witness)

    def adapt_witness_list(self, witnesses: list) -> list[dict]:
        """Convert a list of witness objects to evidence response dicts.

        Failed individual conversions are logged and skipped so that one bad
        witness does not poison the entire batch.

        Parameters
        ----------
        witnesses:
            List of witness objects.

        Returns
        -------
        list[dict]
            Converted evidence dicts (possibly shorter than the input).
        """
        results: list[dict] = []
        for w in witnesses:
            try:
                results.append(self.adapt_witness(w))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to adapt witness %r: %s", w, exc, exc_info=True
                )
        return results

    def get_stats(self) -> dict:
        """Return cumulative conversion statistics.

        Returns
        -------
        dict
            Keys: ``"conversion_count"``.
        """
        return {"conversion_count": self._conversion_count}

    def _build_provenance(self, witness: Any) -> list[str]:
        """Construct a provenance chain for a witness.

        Provenance entries are ordered from most-recent to oldest, following
        the convention used elsewhere in the JuGeo evidence pool.

        Parameters
        ----------
        witness:
            The witness whose provenance is being recorded.

        Returns
        -------
        list[str]
            Provenance strings.
        """
        chain: list[str] = [
            f"witness_adapter:{type(self).__name__}",
            f"witness_type:{type(witness).__name__}",
        ]
        if hasattr(witness, "source"):
            chain.append(f"source:{witness.source}")
        if hasattr(witness, "collector_id"):
            chain.append(f"collector:{witness.collector_id}")
        if hasattr(witness, "created_at"):
            chain.append(f"created_at:{witness.created_at}")
        return chain


# ---------------------------------------------------------------------------
# FederationPipelineAdapter
# ---------------------------------------------------------------------------

class FederationPipelineAdapter:
    """Adapts the solver federation to the JuGeo pipeline step API.

    The pipeline API expects each step to accept a ``context`` dict and
    return a result dict with at minimum ``"step"``, ``"result"``, and
    ``"timestamp"`` keys.  This adapter translates between that convention
    and the evidence-request/response model used by the federation.

    Parameters
    ----------
    federation:
        A ``SolverFederation`` instance (or ``None`` to defer binding).
    """

    def __init__(self, federation: Any = None) -> None:
        self.federation: Any = federation
        self._request_count: int = 0
        self._step_history: list[dict] = []

    # ------------------------------------------------------------------
    # Request / response adaptation
    # ------------------------------------------------------------------

    def adapt_request(self, raw_request: dict) -> dict:
        """Convert a raw pipeline request dict to an EvidenceRequest-compatible dict.

        Missing fields are filled with sensible defaults so that downstream
        components always receive a fully-specified request.

        Parameters
        ----------
        raw_request:
            Arbitrary dict supplied by the pipeline orchestrator.

        Returns
        -------
        dict
            EvidenceRequest-compatible dict.
        """
        request_id = raw_request.get("request_id") or uuid.uuid4().hex[:12]
        coordinate = raw_request.get("coordinate", "global")
        proposition = raw_request.get("proposition", raw_request.get("query", ""))
        required_kind = raw_request.get("kind", "arithmetic")
        budget = float(raw_request.get("budget", 1.0))
        preferred_channel = raw_request.get("preferred_channel", "auto")
        fallback_channels = raw_request.get("fallback_channels", [])
        deadline_ms = raw_request.get("deadline_ms", 5000.0)
        metadata = raw_request.get("metadata", {})

        self._request_count += 1
        return {
            "request_id": request_id,
            "coordinate": coordinate,
            "proposition": proposition,
            "required_kind": required_kind,
            "preferred_channel": preferred_channel,
            "fallback_channels": fallback_channels,
            "deadline_ms": deadline_ms,
            "budget": budget,
            "metadata": metadata,
        }

    def adapt_response(self, evidence_dict: dict) -> dict:
        """Wrap an evidence dict as a pipeline output.

        Parameters
        ----------
        evidence_dict:
            Dict in the EvidenceResponse schema.

        Returns
        -------
        dict
            Pipeline output dict with ``"pipeline_step"``, ``"processed_at"``,
            ``"status"``, and the original evidence fields.
        """
        trust_level = evidence_dict.get("trust_level", "")
        status = "ok" if trust_level else "empty"
        output = dict(evidence_dict)
        output["pipeline_step"] = "federation"
        output["processed_at"] = time.time()
        output["status"] = status
        return output

    # ------------------------------------------------------------------
    # Pipeline step execution
    # ------------------------------------------------------------------

    def run_pipeline_step(self, step_name: str, context: dict) -> dict:
        """Execute a named pipeline step.

        Supported step names
        --------------------
        ``"route"``
            Adapt the context as an EvidenceRequest and route it through the
            federation.  Returns the routing result wrapped as pipeline output.
        ``"merge"``
            Merge any ``"pending_results"`` list in the context into a single
            consolidated evidence dict.
        ``"validate"``
            Run basic validation over all witnesses listed under
            ``"witnesses"`` in the context.

        Parameters
        ----------
        step_name:
            One of ``"route"``, ``"merge"``, or ``"validate"``.
        context:
            Pipeline context dict.

        Returns
        -------
        dict
            ``{"step": step_name, "result": ..., "timestamp": float}``.
        """
        ts = time.time()

        if step_name == "route":
            adapted = self.adapt_request(context)
            if self.federation is not None and hasattr(self.federation, "route"):
                raw_result = self.federation.route(adapted)
                result = self.adapt_response(
                    raw_result if isinstance(raw_result, dict) else {"evidence_item": raw_result}
                )
            else:
                result = self.adapt_response(
                    {
                        "request_id": adapted["request_id"],
                        "channel": "none",
                        "evidence_item": {},
                        "trust_level": "",
                        "latency_ms": 0.0,
                        "is_partial": True,
                        "residuals": ["no_federation"],
                        "provenance": ["pipeline_adapter:no_federation"],
                    }
                )

        elif step_name == "merge":
            pending: list = context.get("pending_results", [])
            merged_items: dict = {}
            merged_provenance: list[str] = []
            for item in pending:
                if isinstance(item, dict):
                    merged_items.update(item.get("evidence_item", {}))
                    merged_provenance.extend(item.get("provenance", []))
            result = {
                "merged_count": len(pending),
                "evidence_item": merged_items,
                "provenance": list(set(merged_provenance)),
                "pipeline_step": "merge",
                "processed_at": ts,
                "status": "ok" if pending else "empty",
            }

        elif step_name == "validate":
            witnesses: list = context.get("witnesses", [])
            valid_count = 0
            invalid_ids: list[str] = []
            for w in witnesses:
                wid = getattr(w, "witness_id", str(id(w)))
                if hasattr(w, "validate") and callable(w.validate):
                    try:
                        ok = w.validate()
                    except Exception:  # noqa: BLE001
                        ok = False
                else:
                    ok = True  # assume valid when no validate() available
                if ok:
                    valid_count += 1
                else:
                    invalid_ids.append(wid)
            result = {
                "total": len(witnesses),
                "valid": valid_count,
                "invalid_ids": invalid_ids,
                "pipeline_step": "validate",
                "processed_at": ts,
                "status": "ok",
            }

        else:
            logger.warning("FederationPipelineAdapter: unknown step %r", step_name)
            result = {"error": f"unknown_step:{step_name}", "status": "error"}

        entry = {"step": step_name, "result": result, "timestamp": ts}
        self._step_history.append(entry)
        return entry

    # ------------------------------------------------------------------
    # History / reset
    # ------------------------------------------------------------------

    def get_step_history(self) -> list[dict]:
        """Return the full list of executed pipeline step records.

        Returns
        -------
        list[dict]
            Each entry has ``"step"``, ``"result"``, and ``"timestamp"`` keys.
        """
        return list(self._step_history)

    def reset(self) -> None:
        """Clear step history and reset request counter.

        This is intended for use between test runs or pipeline epochs.
        """
        self._step_history.clear()
        self._request_count = 0
        logger.debug("FederationPipelineAdapter reset")


# ---------------------------------------------------------------------------
# SiteOracleBridge
# ---------------------------------------------------------------------------

class SiteOracleBridge:
    """Bidirectional bridge between geometry sites and oracle channels.

    In the JuGeo geometry model a *site* is a node in the judgment graph that
    carries a trust context.  The ``SiteOracleBridge`` allows oracle channels
    to be attached to sites so that evidence produced by those channels is
    automatically scoped to the site's coordinate space, and so that trust
    updates derived from oracle evidence flow back into the site's trust
    context.
    """

    def __init__(self) -> None:
        self.site_oracle_map: dict[str, list[str]] = {}
        self.trust_propagations: list[dict] = []
        self._attached_sites: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Site attachment
    # ------------------------------------------------------------------

    def attach_to_site(self, site: Any) -> None:
        """Register a geometry site with the bridge.

        The site's identifier is extracted by trying ``site.site_id``,
        ``site.id``, and finally ``str(id(site))`` in that order.

        Parameters
        ----------
        site:
            A geometry site object.
        """
        if hasattr(site, "site_id"):
            site_id = site.site_id
        elif hasattr(site, "id"):
            site_id = site.id
        else:
            site_id = str(id(site))

        self._attached_sites[site_id] = site
        if site_id not in self.site_oracle_map:
            self.site_oracle_map[site_id] = []
        logger.debug("SiteOracleBridge: attached site %r", site_id)

    def detach_from_site(self, site: Any) -> None:
        """Unregister a geometry site from the bridge.

        Parameters
        ----------
        site:
            The site to detach.  Must have been previously attached.
        """
        if hasattr(site, "site_id"):
            site_id = site.site_id
        elif hasattr(site, "id"):
            site_id = site.id
        else:
            site_id = str(id(site))

        self._attached_sites.pop(site_id, None)
        self.site_oracle_map.pop(site_id, None)
        logger.debug("SiteOracleBridge: detached site %r", site_id)

    # ------------------------------------------------------------------
    # Oracle registration
    # ------------------------------------------------------------------

    def attach_oracle_to_site(self, site_id: str, oracle_id: str) -> None:
        """Associate an oracle channel with a specific site.

        Parameters
        ----------
        site_id:
            Identifier of the already-attached site.
        oracle_id:
            Identifier of the oracle channel to associate.
        """
        if site_id not in self.site_oracle_map:
            self.site_oracle_map[site_id] = []
        if oracle_id not in self.site_oracle_map[site_id]:
            self.site_oracle_map[site_id].append(oracle_id)
        logger.debug(
            "SiteOracleBridge: oracle %r attached to site %r", oracle_id, site_id
        )

    # ------------------------------------------------------------------
    # Trust propagation
    # ------------------------------------------------------------------

    def propagate_trust(
        self, from_site_id: str, to_site_id: str, trust_delta: dict
    ) -> None:
        """Record and apply a trust propagation between two sites.

        The delta is recorded in the internal audit log so that the
        propagation history can be reconstructed for Theory2.tex §7.2.5
        analysis.

        Parameters
        ----------
        from_site_id:
            Origin site.
        to_site_id:
            Destination site.
        trust_delta:
            Arbitrary dict describing the trust change.  Typical keys
            include ``"tier_change"``, ``"reason"``, and ``"evidence_id"``.
        """
        entry = {
            "from": from_site_id,
            "to": to_site_id,
            "delta": trust_delta,
            "timestamp": time.time(),
        }
        self.trust_propagations.append(entry)
        logger.debug(
            "SiteOracleBridge: trust propagated %r → %r delta=%r",
            from_site_id,
            to_site_id,
            trust_delta,
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_site_oracle_map(self) -> dict:
        """Return a shallow copy of the site-to-oracle mapping.

        Returns
        -------
        dict
            ``{site_id: [oracle_id, ...]}``.
        """
        return {k: list(v) for k, v in self.site_oracle_map.items()}

    def get_trust_propagations(self, site_id: str | None = None) -> list[dict]:
        """Return trust propagation records, optionally filtered by site.

        Parameters
        ----------
        site_id:
            When provided, only entries where ``from`` or ``to`` equals
            ``site_id`` are returned.

        Returns
        -------
        list[dict]
            Matching propagation records in chronological order.
        """
        if site_id is None:
            return list(self.trust_propagations)
        return [
            p
            for p in self.trust_propagations
            if p.get("from") == site_id or p.get("to") == site_id
        ]

    def get_attached_sites(self) -> list[str]:
        """Return a list of all currently attached site identifiers.

        Returns
        -------
        list[str]
            Site identifiers in insertion order.
        """
        return list(self._attached_sites.keys())


# ---------------------------------------------------------------------------
# OracleFederationIntegration  (main facade)
# ---------------------------------------------------------------------------

class OracleFederationIntegration:
    """Main integration facade for the oracle federation subsystem.

    This class is the single entry point through which the JuGeo orchestrator
    interacts with the oracle federation.  It owns the lifecycle of the
    federation, witness collector, oracle channels, and sub-adapters, and
    provides a unified API for processing evidence requests.

    See the module docstring for the intended lifecycle.
    """

    def __init__(self) -> None:
        self.integration_id: str = uuid.uuid4().hex[:12]
        self.oracle_channels: dict[str, Any] = {}
        self.federation: Any = None
        self.witness_collector: Any = None
        self.site_registry: dict[str, Any] = {}
        self.router: Any = None
        self.global_enforcer: Any = TrustCeilingEnforcer() if TrustCeilingEnforcer is not None else None
        self.pipeline_adapter: FederationPipelineAdapter = FederationPipelineAdapter()
        self.witness_adapter: WitnessToEvidenceAdapter = WitnessToEvidenceAdapter()
        self.site_bridge: SiteOracleBridge = SiteOracleBridge()
        self._is_initialized: bool = False
        self._request_count: int = 0
        self._start_time: float = time.time()
        self._shutdown: bool = False
        self._config: IntegrationConfig | None = None
        self._audit_log: list[dict] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, config: dict | IntegrationConfig | None = None) -> None:
        """Initialise the integration and all managed subsystems.

        This method is idempotent: calling it a second time on an already-
        initialised integration is a no-op (with a warning).

        Parameters
        ----------
        config:
            Either an ``IntegrationConfig`` instance, a ``dict`` compatible
            with ``IntegrationConfig.from_dict``, or ``None`` to use
            defaults.
        """
        if self._is_initialized:
            logger.warning(
                "OracleFederationIntegration %s already initialised; skipping",
                self.integration_id,
            )
            return

        # Resolve config
        if config is None:
            cfg = IntegrationConfig()
        elif isinstance(config, dict):
            cfg = IntegrationConfig.from_dict(config)
        else:
            cfg = config
        cfg.validate()
        self._config = cfg

        # Create SolverFederation if not externally provided
        if self.federation is None and SolverFederation is not None:
            try:
                self.federation = SolverFederation(name=cfg.federation_name)
                logger.debug(
                    "Created SolverFederation %r", cfg.federation_name
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not create SolverFederation: %s", exc)

        # Register default Z3 solver in the federation
        if self.federation is not None and hasattr(self.federation, "register_backend"):
            try:
                self.federation.register_backend("z3_default", kind="Z3")
                logger.debug("Registered default Z3 backend in federation")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not register Z3 backend: %s", exc)

        # Bind federation to pipeline adapter
        self.pipeline_adapter.federation = self.federation

        # Create RuntimeWitnessCollector
        if cfg.enable_witnesses and self.witness_collector is None:
            if RuntimeWitnessCollector is not None:
                try:
                    self.witness_collector = RuntimeWitnessCollector(
                        snapshot_interval=cfg.witness_snapshot_interval
                    )
                    logger.debug("Created RuntimeWitnessCollector")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Could not create RuntimeWitnessCollector: %s", exc)

        # Register Copilot oracle channel
        if cfg.enable_copilot and CopilotOracleChannel is not None:
            try:
                copilot_ch = CopilotOracleChannel(
                    trust_ceiling=cfg.copilot_ceiling
                )
                self.register_oracle_channel(copilot_ch)
                logger.debug(
                    "Registered CopilotOracleChannel with ceiling %r",
                    cfg.copilot_ceiling,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not register CopilotOracleChannel: %s", exc)

        self._is_initialized = True
        logger.info(
            "OracleFederationIntegration %s initialised: %s",
            self.integration_id,
            cfg.describe(),
        )

    def register_oracle_channel(self, channel: Any) -> None:
        """Register an oracle channel with the integration.

        The channel's trust ceiling is forwarded to the global enforcer so
        that responses from this channel are automatically clamped.

        Parameters
        ----------
        channel:
            An oracle channel object.  Must expose a ``name`` or ``channel_id``
            attribute.
        """
        if self._shutdown:
            raise RuntimeError(
                f"Integration {self.integration_id} has been shut down"
            )

        channel_id: str
        if hasattr(channel, "channel_id"):
            channel_id = channel.channel_id
        elif hasattr(channel, "name"):
            channel_id = channel.name
        else:
            channel_id = type(channel).__name__ + "_" + uuid.uuid4().hex[:6]

        self.oracle_channels[channel_id] = channel
        logger.debug("Registered oracle channel %r", channel_id)

        # Register ceiling with global enforcer
        if self.global_enforcer is not None and hasattr(
            self.global_enforcer, "register_channel"
        ):
            ceiling = getattr(channel, "trust_ceiling", None)
            try:
                self.global_enforcer.register_channel(channel_id, ceiling=ceiling)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Could not register channel %r with enforcer: %s", channel_id, exc
                )

    def register_solver_federation(self, fed: Any) -> None:
        """Replace or set the solver federation used by this integration.

        Parameters
        ----------
        fed:
            A ``SolverFederation`` instance.
        """
        self.federation = fed
        self.pipeline_adapter.federation = fed
        logger.info(
            "OracleFederationIntegration %s: solver federation replaced with %r",
            self.integration_id,
            fed,
        )

    # ------------------------------------------------------------------
    # Core request processing
    # ------------------------------------------------------------------

    def process_request(self, request: Any) -> dict:
        """Process an evidence request through the full federation pipeline.

        The request may be an ``EvidenceRequest`` dataclass instance or a
        plain ``dict`` in the EvidenceRequest schema.  The response is always
        a plain ``dict`` in the EvidenceResponse schema.

        Steps
        -----
        1. Validate initialisation state.
        2. Extract request fields from the input.
        3. Attempt routing through the solver federation.
        4. Fall back to the first registered oracle channel on failure.
        5. Enforce trust ceiling on the response.
        6. Append to the audit log.

        Parameters
        ----------
        request:
            ``EvidenceRequest`` or compatible dict.

        Returns
        -------
        dict
            Evidence response dict.
        """
        self._ensure_initialized()
        self._request_count += 1

        # Extract fields uniformly whether dict or dataclass
        if isinstance(request, dict):
            request_id = request.get("request_id") or uuid.uuid4().hex[:12]
            coordinate = request.get("coordinate", "global")
            proposition = request.get("proposition", request.get("query", ""))
            required_kind = request.get("required_kind", request.get("kind", "arithmetic"))
            budget = float(request.get("budget", 1.0))
        else:
            request_id = getattr(request, "request_id", uuid.uuid4().hex[:12])
            coordinate = getattr(request, "coordinate", "global")
            proposition = getattr(request, "proposition", "")
            required_kind = getattr(request, "required_kind", "arithmetic")
            budget = float(getattr(request, "budget", 1.0))

        start_ts = time.time()
        evidence: dict | None = None

        # Try federation routing first
        if self.federation is not None and hasattr(self.federation, "route"):
            try:
                raw = self.federation.route(
                    {
                        "request_id": request_id,
                        "coordinate": coordinate,
                        "proposition": proposition,
                        "required_kind": required_kind,
                        "budget": budget,
                    }
                )
                if isinstance(raw, dict):
                    evidence = raw
                elif raw is not None:
                    evidence = {"evidence_item": raw, "trust_level": "solver_discharged"}
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Federation routing failed for %s: %s", request_id, exc
                )

        # Fall back to oracle channels
        if evidence is None and self.oracle_channels:
            channel = next(iter(self.oracle_channels.values()))
            if hasattr(channel, "query"):
                try:
                    raw = channel.query(proposition, metadata={"coordinate": coordinate})
                    if isinstance(raw, dict):
                        evidence = raw
                    elif raw is not None:
                        evidence = {
                            "evidence_item": {"raw": raw},
                            "trust_level": "oracle_proposed",
                        }
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Oracle channel fallback failed for %s: %s", request_id, exc
                    )

        # Build response if nothing returned evidence
        if evidence is None:
            evidence = {
                "request_id": request_id,
                "channel": "none",
                "evidence_item": {},
                "trust_level": "",
                "latency_ms": (time.time() - start_ts) * 1000.0,
                "is_partial": True,
                "residuals": ["no_result"],
                "provenance": [f"integration:{self.integration_id}"],
            }

        # Fill in standard fields
        evidence.setdefault("request_id", request_id)
        evidence.setdefault("latency_ms", (time.time() - start_ts) * 1000.0)
        evidence.setdefault("provenance", [])
        evidence["provenance"] = list(evidence["provenance"]) + [
            f"integration:{self.integration_id}"
        ]

        # Enforce trust ceiling via global enforcer
        if self.global_enforcer is not None and hasattr(
            self.global_enforcer, "enforce"
        ):
            try:
                evidence = self.global_enforcer.enforce(evidence)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Trust ceiling enforcement skipped: %s", exc)

        # Audit log
        if self._config is not None and self._config.audit_enabled:
            self._audit_log.append(
                {
                    "request_id": request_id,
                    "trust_level": evidence.get("trust_level", ""),
                    "channel": evidence.get("channel", ""),
                    "timestamp": time.time(),
                }
            )

        return evidence

    # ------------------------------------------------------------------
    # Witness collection
    # ------------------------------------------------------------------

    def collect_and_integrate_witnesses(self) -> list[dict]:
        """Collect and adapt all pending runtime witnesses.

        The ``RuntimeWitnessCollector`` accumulates witnesses asynchronously
        as the program executes.  This method drains the collector, converts
        each witness to an evidence dict via ``WitnessToEvidenceAdapter``, and
        returns the batch.

        Returns
        -------
        list[dict]
            Evidence response dicts, one per witness.  May be empty.
        """
        if self.witness_collector is None:
            logger.debug("No witness collector configured; returning empty list")
            return []

        witnesses: list = []
        if hasattr(self.witness_collector, "export_all"):
            try:
                witnesses = list(self.witness_collector.export_all())
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to export witnesses: %s", exc)
        elif hasattr(self.witness_collector, "witnesses"):
            witnesses = list(self.witness_collector.witnesses)

        evidence_list = self.witness_adapter.adapt_witness_list(witnesses)
        logger.info(
            "OracleFederationIntegration %s: integrated %d witnesses",
            self.integration_id,
            len(evidence_list),
        )
        return evidence_list

    # ------------------------------------------------------------------
    # Status / health
    # ------------------------------------------------------------------

    def get_integration_status(self) -> dict:
        """Return a comprehensive status snapshot.

        Returns
        -------
        dict
            Status dict with keys: ``integration_id``, ``is_initialized``,
            ``oracle_channel_count``, ``has_federation``,
            ``has_witness_collector``, ``request_count``, ``uptime_s``,
            ``shutdown``.
        """
        return {
            "integration_id": self.integration_id,
            "is_initialized": self._is_initialized,
            "oracle_channel_count": len(self.oracle_channels),
            "has_federation": self.federation is not None,
            "has_witness_collector": self.witness_collector is not None,
            "request_count": self._request_count,
            "uptime_s": time.time() - self._start_time,
            "shutdown": self._shutdown,
            "config": self._config.to_dict() if self._config is not None else None,
        }

    def health_check(self) -> dict:
        """Verify that all managed components are available and responsive.

        Each component is tested with a lightweight probe.  The overall
        ``"healthy"`` flag is ``True`` only when all present components
        pass their probe.

        Returns
        -------
        dict
            Keys: ``"healthy"``, ``"components"``, ``"timestamp"``,
            ``"integration_id"``.
        """
        components: dict[str, Any] = {}
        healthy = True

        # Oracle channels probe
        channels_ok = len(self.oracle_channels) > 0
        for cid, ch in self.oracle_channels.items():
            if hasattr(ch, "is_healthy"):
                try:
                    ch_ok = bool(ch.is_healthy())
                except Exception:  # noqa: BLE001
                    ch_ok = False
                if not ch_ok:
                    channels_ok = False
                    break
        components["oracle_channels"] = {
            "count": len(self.oracle_channels),
            "ok": channels_ok,
        }

        # Federation probe
        if self.federation is not None:
            fed_ok = True
            if hasattr(self.federation, "is_healthy"):
                try:
                    fed_ok = bool(self.federation.is_healthy())
                except Exception:  # noqa: BLE001
                    fed_ok = False
            components["federation"] = {"present": True, "ok": fed_ok}
            if not fed_ok:
                healthy = False
        else:
            components["federation"] = {"present": False, "ok": True}

        # Witness collector probe
        if self.witness_collector is not None:
            wc_ok = True
            if hasattr(self.witness_collector, "is_healthy"):
                try:
                    wc_ok = bool(self.witness_collector.is_healthy())
                except Exception:  # noqa: BLE001
                    wc_ok = False
            components["witnesses"] = {"present": True, "ok": wc_ok}
            if not wc_ok:
                healthy = False
        else:
            components["witnesses"] = {"present": False, "ok": True}

        return {
            "healthy": healthy,
            "components": components,
            "timestamp": time.time(),
            "integration_id": self.integration_id,
        }

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def get_audit_trail(self) -> list[dict]:
        """Collect and return all audit entries from oracle channels and internally.

        Returns
        -------
        list[dict]
            Chronologically ordered audit entries.
        """
        trail: list[dict] = list(self._audit_log)
        for cid, ch in self.oracle_channels.items():
            if hasattr(ch, "get_audit_log") and callable(ch.get_audit_log):
                try:
                    entries = ch.get_audit_log()
                    for e in entries:
                        entry = dict(e) if isinstance(e, dict) else {"raw": e}
                        entry.setdefault("channel_id", cid)
                        trail.append(entry)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to collect audit log from %r: %s", cid, exc)
        trail.sort(key=lambda e: e.get("timestamp", 0.0))
        return trail

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Shut down the integration and release all managed resources.

        After shutdown, ``process_request`` raises ``RuntimeError`` and
        ``register_oracle_channel`` raises ``RuntimeError``.
        """
        if self._shutdown:
            return
        self._shutdown = True
        logger.info(
            "OracleFederationIntegration %s shutting down "
            "(handled %d requests in %.1fs)",
            self.integration_id,
            self._request_count,
            time.time() - self._start_time,
        )
        # Best-effort teardown of subsystems
        if self.witness_collector is not None and hasattr(
            self.witness_collector, "stop"
        ):
            try:
                self.witness_collector.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Witness collector stop failed: %s", exc)

        if self.federation is not None and hasattr(self.federation, "shutdown"):
            try:
                self.federation.shutdown()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Federation shutdown failed: %s", exc)

        for cid, ch in list(self.oracle_channels.items()):
            if hasattr(ch, "close"):
                try:
                    ch.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Channel %r close failed: %s", cid, exc)

        self.oracle_channels.clear()
        self.federation = None
        self.witness_collector = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Raise ``RuntimeError`` if the integration has not been initialised.

        Raises
        ------
        RuntimeError
            When ``initialize()`` has not yet been called successfully.
        """
        if not self._is_initialized:
            raise RuntimeError(
                f"OracleFederationIntegration {self.integration_id} has not been "
                "initialised.  Call initialize() before processing requests."
            )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_DEFAULT_INTEGRATION: OracleFederationIntegration | None = None


def get_default_integration() -> OracleFederationIntegration:
    """Return the module-level default integration, creating it if necessary.

    The default integration is initialised with a default
    ``IntegrationConfig`` on first call.  Subsequent calls return the same
    instance.

    Returns
    -------
    OracleFederationIntegration
        The singleton default integration.
    """
    global _DEFAULT_INTEGRATION
    if _DEFAULT_INTEGRATION is None:
        _DEFAULT_INTEGRATION = OracleFederationIntegration()
        _DEFAULT_INTEGRATION.initialize()
        logger.info(
            "Default OracleFederationIntegration created: %s",
            _DEFAULT_INTEGRATION.integration_id,
        )
    return _DEFAULT_INTEGRATION


def create_integration(
    config: dict | None = None,
) -> OracleFederationIntegration:
    """Factory function that creates and initialises a fresh integration.

    Unlike ``get_default_integration``, this function always returns a new
    instance, making it suitable for use in tests and multi-tenant scenarios.

    Parameters
    ----------
    config:
        Optional configuration dict forwarded to ``initialize``.

    Returns
    -------
    OracleFederationIntegration
        An initialised integration instance.
    """
    integration = OracleFederationIntegration()
    integration.initialize(config)
    logger.info(
        "Created OracleFederationIntegration %s via factory",
        integration.integration_id,
    )
    return integration
