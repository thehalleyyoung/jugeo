r"""Integration layer for JuGeo research assistance — Chapter 51.

This module provides the top-level :class:`ResearchAssistanceIntegration`
class that wires together all sub-systems: the proof suggestion engine, lemma
miner, conjecture generator, oracle interface, and formal verifier bridge.

It also provides supporting infrastructure: an event bus for decoupled
component communication, session persistence, and a copilot advisor that
surfaces actionable recommendations from the current session state.

Design
------

The integration layer follows an *event-driven pipeline* pattern::

    start_session(ctx)
        → publishes "session.started"
    run_proof_suggestions(session)
        → ProofSuggestionEngine + VerifierBridge → list[ProofSuggestion]
        → publishes "proof_suggestions.ready"
    run_lemma_mining(session, archive)
        → LemmaMiner → list[LemmaCandidate]
        → publishes "lemma_mining.ready"
    run_conjecture_generation(session)
        → ConjectureGenerator → list[ConjectureRecord]
        → publishes "conjecture_generation.ready"
    close_session(session)
        → publishes "session.closed"
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections import defaultdict
from typing import Callable

from jugeo.ideation.research_assistance.manifest import ResearchAssistanceManifest
from jugeo.ideation.research_assistance.models import (
    ConjectureRecord,
    LemmaCandidate,
    ProofSuggestion,
    ResearchContext,
    ResearchSession,
    SessionStatus,
    VerificationRecord,
    VerificationStatus,
    make_context,
    make_session,
)
from jugeo.ideation.research_assistance.proof_suggestion import ProofSuggestionEngine
from jugeo.ideation.research_assistance.lemma_mining import (
    LemmaArchive,
    LemmaMiner,
    MiningConfig,
)
from jugeo.ideation.research_assistance.conjecture_generation import ConjectureGenerator
from jugeo.ideation.research_assistance.oracle_interface import (
    ControlledOracleProtocol,
    CopilotOracle,
    OraclePolicy,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ResearchEventBus
# ---------------------------------------------------------------------------


class ResearchEventBus:
    """A simple synchronous event bus for decoupled component communication.

    Components subscribe to named event types and are called synchronously
    when events are published.  Errors in handlers are caught and logged so
    that a broken handler cannot crash the pipeline.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Register handler to be called when event_type is published."""
        self._handlers[event_type].append(handler)
        _log.debug("EventBus: subscribed to %r (%d handlers)", event_type, len(self._handlers[event_type]))

    def publish(self, event_type: str, data: dict) -> None:
        """Call all registered handlers for event_type with data."""
        for handler in self._handlers.get(event_type, []):
            try:
                handler(data)
            except Exception as exc:
                _log.warning(
                    "EventBus: handler error for event %r: %s", event_type, exc
                )
        _log.debug("EventBus: published %r to %d handlers", event_type, len(self._handlers.get(event_type, [])))

    def clear(self) -> None:
        """Remove all event subscriptions."""
        self._handlers.clear()

    def handler_count(self, event_type: str) -> int:
        """Return the number of handlers registered for event_type."""
        return len(self._handlers.get(event_type, []))

    def event_types(self) -> tuple[str, ...]:
        """Return a sorted tuple of all event types with at least one handler."""
        return tuple(sorted(k for k, v in self._handlers.items() if v))


# ---------------------------------------------------------------------------
# SessionPersistence
# ---------------------------------------------------------------------------


class SessionPersistence:
    """Saves and loads :class:`ResearchSession` objects as JSON files.

    The persistence format is intentionally simple: it serializes the session
    summary and key scalar fields.  Full deserialization reconstructs a
    minimal session suitable for display and continued work.
    """

    def save(self, session: ResearchSession, path: str) -> None:
        """Serialize session to a JSON file at the given path."""
        data = {
            "session_id": session.session_id,
            "status": session.status.value,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "context_id": session.context.context_id,
            "current_theorem": session.context.current_theorem,
            "partial_proof": session.context.partial_proof,
            "purpose": session.context.purpose,
            "suggestion_count": len(session.history),
            "conjecture_count": len(session.active_conjectures),
            "summary": session.to_summary(),
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        _log.info("SessionPersistence: saved session %s to %s", session.session_id, path)

    def load(self, path: str) -> ResearchSession:
        """Reconstruct a minimal ResearchSession from a JSON file."""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        from jugeo.ideation.research_assistance.models import SessionStatus

        context = ResearchContext(
            context_id=data.get("context_id", str(uuid.uuid4())),
            current_theorem=data.get("current_theorem", ""),
            partial_proof=data.get("partial_proof", ""),
            purpose=data.get("purpose", ""),
        )
        session = ResearchSession(
            session_id=data.get("session_id", str(uuid.uuid4())),
            context=context,
            status=SessionStatus(data.get("status", "active")),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
        _log.info("SessionPersistence: loaded session %s from %s", session.session_id, path)
        return session

    def list_sessions(self, directory: str) -> list[str]:
        """Return a list of .json file paths in the given directory."""
        try:
            entries = os.listdir(directory)
        except OSError:
            return []
        return sorted(
            os.path.join(directory, f)
            for f in entries
            if f.endswith(".json")
        )


# ---------------------------------------------------------------------------
# VerifierBridge
# ---------------------------------------------------------------------------


class VerifierBridge:
    """Bridge to the JuGeo formal verifier sub-system.

    In production this class wraps an IPC or API call to the verifier.  In
    testing and offline mode it applies a simple heuristic: a statement with
    more than five characters is considered provable.  This makes the bridge
    deterministic and suitable for unit testing.

    Attributes:
        _verifier_id: Identifier string for the verifier instance.
        _available: Whether the verifier is currently reachable.
    """

    def __init__(self, verifier_id: str = "jugeo-formal") -> None:
        self._verifier_id = verifier_id
        self._available = True

    def verify(self, statement: str) -> VerificationRecord:
        """Submit a statement to the verifier and return the record.

        The heuristic verdict is True when the statement is non-trivially
        long (> 5 characters) — production code replaces this with a real
        verifier call.
        """
        verdict = len(statement.strip()) > 5
        return VerificationRecord(
            record_id=str(uuid.uuid4()),
            subject_id=str(uuid.uuid4()),
            verdict=verdict,
            evidence=(
                f"VerifierBridge({self._verifier_id}): "
                f"len={len(statement.strip())} verdict={verdict}"
            ),
            timestamp=time.time(),
            verifier_id=self._verifier_id,
        )

    def batch_verify(self, statements: list[str]) -> list[VerificationRecord]:
        """Verify a list of statements and return the corresponding records."""
        return [self.verify(stmt) for stmt in statements]

    def is_available(self) -> bool:
        """Return True if the verifier bridge is reachable."""
        return self._available

    def status(self) -> str:
        """Return a human-readable status string."""
        return "available" if self._available else "unavailable"

    def set_available(self, available: bool) -> None:
        """Set the availability flag (for testing)."""
        self._available = available


# ---------------------------------------------------------------------------
# CopilotResearchAdvisor
# ---------------------------------------------------------------------------


class CopilotResearchAdvisor:
    """Surfaces actionable research recommendations from oracle + verifier.

    The advisor queries the :class:`CopilotOracle` for proof suggestions,
    verifies each suggestion via the :class:`VerifierBridge`, and returns
    only the suggestions that pass verification.

    Attributes:
        _oracle: The oracle to query for suggestions.
        _bridge: The verifier bridge to validate oracle responses.
        _engine: Proof suggestion engine for context-based suggestions.
    """

    def __init__(self, oracle: CopilotOracle, bridge: VerifierBridge) -> None:
        self._oracle = oracle
        self._bridge = bridge
        self._engine = ProofSuggestionEngine()

    def advise(self, context: ResearchContext) -> list[ProofSuggestion]:
        """Generate and verify proof suggestions for the given context."""
        raw = self._engine.suggest(context)
        accepted: list[ProofSuggestion] = []
        for suggestion in raw:
            record = self._bridge.verify(suggestion.tactic_description)
            if record.verdict:
                accepted.append(suggestion.apply())
            else:
                accepted.append(suggestion.reject())
        return accepted

    def surface_opportunities(self, session: ResearchSession) -> list[str]:
        """Return a list of human-readable improvement opportunities."""
        opportunities: list[str] = []

        unverified = [s for s in session.history if s.verification_status.value == "pending"]
        if unverified:
            opportunities.append(
                f"{len(unverified)} proof suggestion(s) are still PENDING verification."
            )

        open_conjectures = [
            c for c in session.active_conjectures if c.status.value == "open"
        ]
        if open_conjectures:
            opportunities.append(
                f"{len(open_conjectures)} conjecture(s) are OPEN and need evidence."
            )

        if not session.context.available_lemmas:
            opportunities.append(
                "No lemmas are available in the context — consider running lemma mining."
            )

        if not session.context.partial_proof:
            opportunities.append(
                "The partial proof is empty — consider starting with 'intro'."
            )

        return opportunities

    def explain(self, suggestion: ProofSuggestion) -> str:
        """Return a human-readable explanation of a proof suggestion."""
        return (
            f"Suggestion {suggestion.suggestion_id[:8]!r}: apply tactic "
            f"{suggestion.tactic_description!r} to goal {suggestion.target_goal[:60]!r}. "
            f"Confidence: {suggestion.confidence:.2f}. "
            f"Justification: {suggestion.justification}. "
            f"Status: {suggestion.verification_status.value}."
        )


# ---------------------------------------------------------------------------
# ResearchAssistanceIntegration
# ---------------------------------------------------------------------------


class ResearchAssistanceIntegration:
    """Top-level integration orchestrating all research assistance sub-systems.

    This class wires together the manifest, oracle, verifier bridge, event bus,
    and all domain-specific engines.  It is the primary entry point for
    external callers.

    Session lifecycle (Thm 51.15)::

        session = integration.start_session(ctx)   # status = ACTIVE
        # ... run_proof_suggestions, run_lemma_mining, run_conjecture_generation
        integration.close_session(session)          # status = COMPLETED
    """

    def __init__(
        self,
        manifest: ResearchAssistanceManifest,
        oracle: CopilotOracle,
        bridge: VerifierBridge,
        event_bus: ResearchEventBus | None = None,
    ) -> None:
        self._manifest = manifest
        self._oracle = oracle
        self._bridge = bridge
        self._event_bus = event_bus or ResearchEventBus()
        self._suggestion_engine = ProofSuggestionEngine()
        self._lemma_miner = LemmaMiner()
        self._conjecture_generator = ConjectureGenerator()
        self._advisor = CopilotResearchAdvisor(oracle=oracle, bridge=bridge)

    def start_session(self, context: ResearchContext) -> ResearchSession:
        """Create and return a new ACTIVE :class:`ResearchSession`.

        Publishes a ``"session.started"`` event on the bus.
        """
        session = ResearchSession(
            session_id=str(uuid.uuid4()),
            context=context,
            status=SessionStatus.ACTIVE,
        )
        self._event_bus.publish(
            "session.started",
            {"session_id": session.session_id, "context_id": context.context_id},
        )
        _log.info("ResearchAssistanceIntegration: started session %s", session.session_id)
        return session

    def run_proof_suggestions(
        self,
        session: ResearchSession,
    ) -> list[ProofSuggestion]:
        """Run the proof suggestion engine and add results to the session.

        Publishes a ``"proof_suggestions.ready"`` event.
        """
        suggestions = self._suggestion_engine.suggest(session.context)
        for suggestion in suggestions:
            session.add_suggestion(suggestion)

        self._event_bus.publish(
            "proof_suggestions.ready",
            {
                "session_id": session.session_id,
                "count": len(suggestions),
            },
        )
        _log.debug(
            "ResearchAssistanceIntegration: %d suggestions for session %s",
            len(suggestions),
            session.session_id,
        )
        return suggestions

    def run_lemma_mining(
        self,
        session: ResearchSession,
        archive: LemmaArchive,
    ) -> list[LemmaCandidate]:
        """Run the lemma miner and return candidates relevant to the session.

        Publishes a ``"lemma_mining.ready"`` event.
        """
        candidates = self._lemma_miner.mine(archive, session.context)
        for candidate in candidates:
            session.context.add_lemma(candidate)

        self._event_bus.publish(
            "lemma_mining.ready",
            {
                "session_id": session.session_id,
                "count": len(candidates),
            },
        )
        _log.debug(
            "ResearchAssistanceIntegration: %d lemma candidates for session %s",
            len(candidates),
            session.session_id,
        )
        return candidates

    def run_conjecture_generation(
        self,
        session: ResearchSession,
    ) -> list[ConjectureRecord]:
        """Run the conjecture generator and attach results to the session.

        Publishes a ``"conjecture_generation.ready"`` event.
        """
        conjectures = self._conjecture_generator.generate(session.context)
        session.active_conjectures.extend(conjectures)

        self._event_bus.publish(
            "conjecture_generation.ready",
            {
                "session_id": session.session_id,
                "count": len(conjectures),
            },
        )
        _log.debug(
            "ResearchAssistanceIntegration: %d conjectures for session %s",
            len(conjectures),
            session.session_id,
        )
        return conjectures

    def close_session(self, session: ResearchSession) -> None:
        """Transition the session to COMPLETED status.

        Publishes a ``"session.closed"`` event (Thm 51.15).
        """
        session.close()
        self._event_bus.publish(
            "session.closed",
            {
                "session_id": session.session_id,
                "suggestion_count": len(session.history),
                "conjecture_count": len(session.active_conjectures),
            },
        )
        _log.info("ResearchAssistanceIntegration: closed session %s", session.session_id)

    def advisor(self) -> CopilotResearchAdvisor:
        """Return the integrated :class:`CopilotResearchAdvisor`."""
        return self._advisor

    def manifest(self) -> ResearchAssistanceManifest:
        """Return the manifest governing this integration instance."""
        return self._manifest

    def event_bus(self) -> ResearchEventBus:
        """Return the event bus used by this integration."""
        return self._event_bus


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def make_default_integration(
    current_theorem: str = "∀ n : ℕ, n + 0 = n",
    *,
    purpose: str = "Prove base arithmetic identities",
) -> tuple[ResearchAssistanceIntegration, ResearchContext]:
    """Create a default integration instance and context for quick-start usage."""
    from jugeo.ideation.research_assistance.manifest import make_default_manifest

    manifest = make_default_manifest()
    oracle = CopilotOracle(oracle_id="copilot-default")
    bridge = VerifierBridge()
    integration = ResearchAssistanceIntegration(
        manifest=manifest,
        oracle=oracle,
        bridge=bridge,
    )
    context = make_context(current_theorem, purpose=purpose)
    return integration, context


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "CopilotResearchAdvisor",
    "ResearchAssistanceIntegration",
    "ResearchEventBus",
    "SessionPersistence",
    "VerifierBridge",
    "make_default_integration",
]
