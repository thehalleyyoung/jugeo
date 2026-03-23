r"""theory2.tex Ch31 — Integration with JuGeo Solver and Evidence Infrastructure.

This module provides bridge classes and session management for integrating
the Ch31 partiality encodings with the JuGeo solver pipeline:

- :class:`PartialityEncodingSession` — manages a Z3 encoding session
- :class:`ModelReconstructionPipeline` — runs reconstruction from Z3 results
- :class:`ExceptionSemanticsBridge` — bridges exception semantics to JuGeo
- :class:`CopilotReconstructionAssist` — copilot integration hook for completion hints

.. math::

   \\text{Session} \\xrightarrow{\\text{encode}} \\text{Z3}
   \\xrightarrow{\\text{solve}} \\text{Model}
   \\xrightarrow{\\text{reconstruct}} \\text{Evidence}
   \\xrightarrow{\\text{package}} \\text{JuGeo}
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
import dataclasses
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Optional jugeo subpackage imports — gracefully degrade when unavailable
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder, Z3Decoder, Z3Result
    _Z3_SESSION_AVAILABLE = True
except ImportError:
    _Z3_SESSION_AVAILABLE = False
    class Z3Session: pass  # type: ignore[misc]
    class Z3Formula: pass  # type: ignore[misc]
    class Z3Encoder: pass  # type: ignore[misc]
    class Z3Decoder: pass  # type: ignore[misc]
    class Z3Result: pass  # type: ignore[misc]

try:
    from jugeo.solver.reconstruction import ModelReconstructor as SolverModelReconstruction
    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    _RECONSTRUCTION_AVAILABLE = False
    class SolverModelReconstruction: pass  # type: ignore[misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, Judgment
    _JUDGMENTS_AVAILABLE = True
except ImportError:
    _JUDGMENTS_AVAILABLE = False
    class JudgmentTerm: pass  # type: ignore[misc]
    class Judgment: pass  # type: ignore[misc]

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False
    class TrustAlgebra: pass  # type: ignore[misc]
    class TrustLevel: pass  # type: ignore[misc]

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SessionState(str, Enum):
    """Lifecycle state of a :class:`PartialityEncodingSession`.

    Transitions:
        IDLE -> ENCODING -> SOLVING -> RECONSTRUCTING -> COMPLETE
        Any state -> ERROR (on unrecoverable failure)

    The session is considered *active* in all states except COMPLETE and ERROR.
    """

    IDLE = "idle"
    ENCODING = "encoding"
    SOLVING = "solving"
    RECONSTRUCTING = "reconstructing"
    COMPLETE = "complete"
    ERROR = "error"


class BridgeStatus(str, Enum):
    """Connection status of an :class:`ExceptionSemanticsBridge`.

    - CONNECTED    : bridge is fully operational
    - DISCONNECTED : bridge has been explicitly disconnected
    - DEGRADED     : bridge is operational but some handlers are missing
    - BYPASSED     : bridge is being deliberately skipped (e.g. for testing)
    """

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    DEGRADED = "degraded"
    BYPASSED = "bypassed"


# ---------------------------------------------------------------------------
# PartialityEncodingSession
# ---------------------------------------------------------------------------


@dataclass
class PartialityEncodingSession:
    """Manages the lifecycle of a single Ch31 partiality encoding session.

    A session tracks the sequence of steps:
    1. Encoding partial functions as Z3 relations
    2. Submitting encoded queries to a Z3 solver
    3. Collecting solver results
    4. Reconstructing evidence from satisfying models

    Each step is guarded by state transitions so that the session can
    diagnose misuse (e.g. submitting results before solving has started).

    Attributes
    ----------
    session_id:
        Unique identifier for this session.  Auto-generated as a UUID string.
    state:
        Current lifecycle state of the session.
    encodings:
        List of encoding dictionaries submitted via :meth:`submit_encoding`.
    results:
        List of result dictionaries submitted via :meth:`submit_result`.
    errors:
        List of error message strings accumulated during the session.
    metadata:
        Freeform metadata dictionary for timestamps and annotations.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: SessionState = SessionState.IDLE
    encodings: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # State transition methods
    # ------------------------------------------------------------------

    def begin_encoding(self) -> None:
        """Transition the session to the ENCODING state.

        Records the encoding start timestamp in :attr:`metadata`.
        """
        self.state = SessionState.ENCODING
        self.metadata["encoding_started_at"] = time.time()

    def submit_encoding(self, encoding_dict: dict[str, Any]) -> str:
        """Submit an encoding dictionary to this session.

        Creates a copy of the encoding dict, assigns a fresh encoding_id,
        and appends it to the internal :attr:`encodings` list.

        Parameters
        ----------
        encoding_dict:
            A partial function encoding dictionary, typically produced by
            :func:`~jugeo.encodings.partiality_model_reconstruction.algorithms.encode_partial_function`.

        Returns
        -------
        str
            The unique encoding_id assigned to this submission.
        """
        encoding_id = str(uuid.uuid4())
        # Create a fresh copy so the caller's dict is not mutated
        stored = dict(encoding_dict)
        stored["encoding_id"] = encoding_id
        stored["submitted_at"] = time.time()
        stored["session_id"] = self.session_id
        self.encodings.append(stored)
        # Update metadata with the latest submission info
        self.metadata["last_encoding_submitted_at"] = time.time()
        self.metadata["encoding_count"] = len(self.encodings)
        return encoding_id

    def begin_solving(self) -> None:
        """Transition the session to the SOLVING state.

        Records the solving start timestamp in :attr:`metadata`.
        """
        self.state = SessionState.SOLVING
        self.metadata["solving_started_at"] = time.time()

    def submit_result(self, result_dict: dict[str, Any]) -> None:
        """Append a solver result dictionary to this session.

        Parameters
        ----------
        result_dict:
            A solver result dictionary, e.g. from a Z3 satisfying model.
            The dictionary is stored as-is (not copied), so callers should
            not mutate it after submission.
        """
        self.results.append(result_dict)
        self.metadata["last_result_at"] = time.time()
        self.metadata["result_count"] = len(self.results)

    def begin_reconstruction(self) -> None:
        """Transition the session to the RECONSTRUCTING state.

        Records the reconstruction start timestamp in :attr:`metadata`.
        """
        self.state = SessionState.RECONSTRUCTING
        self.metadata["reconstruction_started_at"] = time.time()

    def complete(self) -> None:
        """Mark the session as successfully completed.

        Records the completion timestamp in :attr:`metadata`.
        """
        self.state = SessionState.COMPLETE
        self.metadata["completed_at"] = time.time()

        # Compute and record a summary hash of all encoding IDs for integrity
        encoding_ids = [e.get("encoding_id", "") for e in self.encodings]
        self.metadata["encoding_fingerprint"] = hashlib.sha256(
            "|".join(encoding_ids).encode()
        ).hexdigest()[:16]

    def fail(self, error: str) -> None:
        """Record an error and transition the session to the ERROR state.

        Parameters
        ----------
        error:
            Human-readable error message describing the failure.
        """
        self.errors.append(error)
        self.state = SessionState.ERROR
        self.metadata["failed_at"] = time.time()
        self.metadata["error_count"] = len(self.errors)

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        """Return True iff the session has not reached a terminal state.

        Terminal states are COMPLETE and ERROR.  All other states are
        considered active.
        """
        return self.state not in (SessionState.COMPLETE, SessionState.ERROR)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a concise summary dictionary for this session.

        Returns
        -------
        dict[str, Any]
            Keys: session_id, state, encoding_count, result_count,
            error_count, is_active, metadata.
        """
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "encoding_count": len(self.encodings),
            "result_count": len(self.results),
            "error_count": len(self.errors),
            "is_active": self.is_active(),
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
        }

    def to_z3_session_input(self) -> list[str]:
        """Extract the SMT2 strings from all submitted encodings.

        Returns a list of SMT2 encoding strings suitable for passing to a
        :class:`~jugeo.solver.z3_session.Z3Session` or similar solver.

        Returns
        -------
        list[str]
            List of SMT2 strings; encodings without an "smt2" key are skipped.
        """
        smt2_strings: list[str] = []
        for encoding in self.encodings:
            smt2 = encoding.get("smt2")
            if smt2 and isinstance(smt2, str):
                smt2_strings.append(smt2)
        return smt2_strings

    def __repr__(self) -> str:
        return (
            f"PartialityEncodingSession("
            f"id={self.session_id[:8]}..., "
            f"state={self.state.value}, "
            f"encodings={len(self.encodings)}, "
            f"results={len(self.results)})"
        )


# ---------------------------------------------------------------------------
# ModelReconstructionPipeline
# ---------------------------------------------------------------------------


@dataclass
class ModelReconstructionPipeline:
    """Orchestrates the full model reconstruction pipeline for Ch31.

    The pipeline ties together a :class:`PartialityEncodingSession` (which
    collects encodings and raw solver results) with the reconstruction logic
    that transforms those results into JuGeo-compatible evidence.

    Attributes
    ----------
    pipeline_id:
        Unique identifier for this pipeline instance.
    session:
        The encoding session providing raw solver results.
    reconstruction_results:
        List of reconstruction output dictionaries.
    trust_ceiling:
        The maximum trust level that may be assigned to any reconstructed
        evidence from this pipeline.  Defaults to "UNVERIFIED".
    """

    pipeline_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session: PartialityEncodingSession = field(
        default_factory=PartialityEncodingSession
    )
    reconstruction_results: list[dict[str, Any]] = field(default_factory=list)
    trust_ceiling: str = "UNVERIFIED"

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest_z3_result(self, z3_result_dict: dict[str, Any]) -> None:
        """Ingest a raw Z3 result dictionary into the pipeline's session.

        Extracts the model assignments (from either a ``"model"`` or
        ``"assignments"`` key) and submits them to the session.

        Parameters
        ----------
        z3_result_dict:
            A dictionary produced by a Z3 solver run.  Expected to contain
            either a ``"model"`` key (dict of variable assignments) or an
            ``"assignments"`` key.
        """
        # Prefer "model" key; fall back to "assignments"; fall back to the whole dict
        if "model" in z3_result_dict and isinstance(z3_result_dict["model"], dict):
            model_payload = z3_result_dict["model"]
        elif "assignments" in z3_result_dict and isinstance(z3_result_dict["assignments"], dict):
            model_payload = z3_result_dict["assignments"]
        else:
            # Use the whole dict as the model if no known key is present
            model_payload = dict(z3_result_dict)

        # Annotate the stored result with ingestion metadata
        result_to_store = {
            "model": model_payload,
            "ingested_at": time.time(),
            "pipeline_id": self.pipeline_id,
            "original_keys": list(z3_result_dict.keys()),
            "result_index": len(self.session.results),
        }
        self.session.submit_result(result_to_store)

    # ------------------------------------------------------------------
    # Reconstruction
    # ------------------------------------------------------------------

    def run_reconstruction(self) -> dict[str, Any]:
        """Run the reconstruction phase over all ingested solver results.

        Iterates over all results in the session, extracts model dicts,
        and merges all variable assignments into a single reconstruction
        output.

        Returns
        -------
        dict[str, Any]
            - ``pipeline_id``       : this pipeline's ID
            - ``reconstruction_id`` : fresh UUID for this reconstruction run
            - ``reconstructed_vars``: number of distinct variables reconstructed
            - ``assignments``       : merged dict of all variable assignments
            - ``reconstructed_at``  : UNIX timestamp
        """
        reconstruction_id = str(uuid.uuid4())
        merged_assignments: dict[str, Any] = {}

        for result in self.session.results:
            model = result.get("model", {})
            if isinstance(model, dict):
                for key, value in model.items():
                    # Last-write-wins merge (simple and consistent)
                    merged_assignments[key] = value

        reconstruction = {
            "pipeline_id": self.pipeline_id,
            "reconstruction_id": reconstruction_id,
            "reconstructed_vars": len(merged_assignments),
            "assignments": merged_assignments,
            "reconstructed_at": time.time(),
            "source_result_count": len(self.session.results),
        }
        self.reconstruction_results.append(reconstruction)
        return reconstruction

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    def apply_trust_ceiling(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Apply the pipeline's trust ceiling to an evidence dictionary.

        Creates a shallow copy of the evidence dict and adds the
        ``"trust_ceiling"`` key.

        Parameters
        ----------
        evidence:
            The evidence dictionary to annotate.

        Returns
        -------
        dict[str, Any]
            A copy of *evidence* with the ``"trust_ceiling"`` key set.
        """
        annotated = dict(evidence)
        annotated["trust_ceiling"] = self.trust_ceiling
        annotated["trust_applied_at"] = time.time()
        annotated["trust_applied_by_pipeline"] = self.pipeline_id
        return annotated

    def package_for_jugeo(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Wrap an evidence dict in the standard JuGeo packaging format.

        Produces a top-level envelope that JuGeo's evidence infrastructure
        expects for Ch31 partiality evidence.

        Parameters
        ----------
        evidence:
            The reconstructed and trust-annotated evidence dict.

        Returns
        -------
        dict[str, Any]
            A JuGeo-compatible package dict.
        """
        package = {
            "type": "partiality_evidence",
            "version": "0.1.0",
            "chapter": "Ch31",
            "pipeline_id": self.pipeline_id,
            "evidence": evidence,
            "packaged_at": time.time(),
            "package_id": str(uuid.uuid4()),
            "schema": {
                "type": "partiality_evidence",
                "chapter": "Ch31",
                "theory_ref": "theory2.tex",
            },
        }
        return package

    # ------------------------------------------------------------------
    # Validation and lifecycle
    # ------------------------------------------------------------------

    def validate_pipeline(self) -> list[str]:
        """Validate the internal consistency of this pipeline.

        Checks that:
        - The session is not in ERROR state
        - If the session is COMPLETE, reconstruction_results is non-empty
        - All reconstruction results have required keys

        Returns
        -------
        list[str]
            A (possibly empty) list of validation error strings.
        """
        validation_errors: list[str] = []

        if self.session.state == SessionState.ERROR:
            validation_errors.append(
                f"Pipeline {self.pipeline_id}: session is in ERROR state with errors: "
                f"{self.session.errors}"
            )

        if (
            self.session.state == SessionState.COMPLETE
            and not self.reconstruction_results
        ):
            validation_errors.append(
                f"Pipeline {self.pipeline_id}: session is COMPLETE but no reconstruction "
                "results have been produced.  Did you call run_reconstruction()?"
            )

        # Check that all reconstruction results have required keys
        required_keys = {"pipeline_id", "reconstruction_id", "assignments"}
        for i, rec in enumerate(self.reconstruction_results):
            missing = required_keys - set(rec.keys())
            if missing:
                validation_errors.append(
                    f"Reconstruction result {i} is missing required keys: {missing}"
                )

        return validation_errors

    def reset(self) -> None:
        """Reset the pipeline to a fresh state while preserving trust_ceiling.

        Clears all reconstruction results and replaces the session with a
        brand-new :class:`PartialityEncodingSession`.  The :attr:`trust_ceiling`
        is preserved.
        """
        self.reconstruction_results = []
        self.session = PartialityEncodingSession()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a summary dict for this pipeline.

        Returns
        -------
        dict[str, Any]
            Keys: pipeline_id, session (summary), result_count, trust_ceiling.
        """
        return {
            "pipeline_id": self.pipeline_id,
            "session": self.session.summary(),
            "result_count": len(self.reconstruction_results),
            "trust_ceiling": self.trust_ceiling,
            "validation_errors": self.validate_pipeline(),
        }

    def __repr__(self) -> str:
        return (
            f"ModelReconstructionPipeline("
            f"id={self.pipeline_id[:8]}..., "
            f"trust_ceiling={self.trust_ceiling!r}, "
            f"results={len(self.reconstruction_results)})"
        )


# ---------------------------------------------------------------------------
# ExceptionSemanticsBridge
# ---------------------------------------------------------------------------


@dataclass
class ExceptionSemanticsBridge:
    """Bridges exception-valued semantics (Ch31 §31.2) to the JuGeo infrastructure.

    Maintains registries of known exception sorts and their handlers,
    and provides translation methods to convert between the Ch31 exception
    representation and the JuGeo evidence format.

    Attributes
    ----------
    bridge_id:
        Unique identifier for this bridge instance.
    status:
        Current operational status of the bridge.
    exception_registry:
        Maps exception sort names to their descriptor dicts.
    handler_registry:
        Maps exception sort names to SMT2 handler expressions.
    """

    bridge_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: BridgeStatus = BridgeStatus.CONNECTED
    exception_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    handler_registry: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------

    def register_exception(self, sort_name: str, info: dict[str, Any]) -> None:
        """Register an exception sort with its descriptor.

        Parameters
        ----------
        sort_name:
            The SMT2 sort name of the exception (e.g. ``"DivisionByZero"``).
        info:
            A dictionary describing the exception sort: constructors,
            arguments, documentation, etc.
        """
        stored_info = dict(info)
        stored_info["registered_at"] = time.time()
        stored_info["bridge_id"] = self.bridge_id
        stored_info["sort_name"] = sort_name
        self.exception_registry[sort_name] = stored_info

    def register_handler(self, sort_name: str, handler_expr: str) -> None:
        """Register an SMT2 handler expression for an exception sort.

        A handler expression is an SMT2 term that describes how to
        recover from or propagate an exception of the given sort.

        Parameters
        ----------
        sort_name:
            The exception sort to handle.
        handler_expr:
            An SMT2 expression string for the handler.
        """
        self.handler_registry[sort_name] = handler_expr

    # ------------------------------------------------------------------
    # Translation
    # ------------------------------------------------------------------

    def translate_to_jugeo(self, exception_dict: dict[str, Any]) -> dict[str, Any]:
        """Translate a Ch31 exception dict to JuGeo's evidence format.

        Adds JuGeo-specific keys to a copy of the exception dictionary.

        Parameters
        ----------
        exception_dict:
            A Ch31 exception descriptor dictionary.

        Returns
        -------
        dict[str, Any]
            A copy of *exception_dict* with JuGeo envelope fields added.
        """
        translated = dict(exception_dict)
        translated["jugeo_type"] = "exception_evidence"
        translated["bridge_id"] = self.bridge_id
        translated["translated_at"] = time.time()
        translated["bridge_status"] = self.status.value
        # Add handler information if available
        sort_name = exception_dict.get("sort_name", "")
        if sort_name and sort_name in self.handler_registry:
            translated["handler_expr"] = self.handler_registry[sort_name]
            translated["has_handler"] = True
        else:
            translated["has_handler"] = False
        return translated

    def translate_from_jugeo(self, jugeo_dict: dict[str, Any]) -> dict[str, Any]:
        """Extract Ch31 exception information from a JuGeo evidence dict.

        Reverses the direction of :meth:`translate_to_jugeo`, stripping
        JuGeo envelope fields and looking up handler information.

        Parameters
        ----------
        jugeo_dict:
            A JuGeo evidence dictionary that wraps a Ch31 exception.

        Returns
        -------
        dict[str, Any]
            A Ch31-format exception dict with sort_name, constructors, and
            handler if available.
        """
        # Reconstruct the original exception dict by removing JuGeo keys
        jugeo_only_keys = {
            "jugeo_type", "bridge_id", "translated_at", "bridge_status",
            "has_handler",
        }
        ch31_dict: dict[str, Any] = {
            k: v for k, v in jugeo_dict.items() if k not in jugeo_only_keys
        }

        sort_name = jugeo_dict.get("sort_name", "")
        constructors: list[str] = []

        if sort_name and sort_name in self.exception_registry:
            reg_info = self.exception_registry[sort_name]
            constructors = reg_info.get("constructors", [])

        ch31_dict["sort_name"] = sort_name
        ch31_dict["constructors"] = constructors

        if sort_name and sort_name in self.handler_registry:
            ch31_dict["handler"] = self.handler_registry[sort_name]

        ch31_dict["extracted_from_jugeo_at"] = time.time()
        return ch31_dict

    # ------------------------------------------------------------------
    # Predicates and queries
    # ------------------------------------------------------------------

    def is_handled(self, sort_name: str) -> bool:
        """Return True iff the given exception sort has a registered handler.

        Parameters
        ----------
        sort_name:
            The exception sort name to check.
        """
        return sort_name in self.handler_registry

    def all_unhandled(self) -> list[str]:
        """Return a list of registered exception sorts without handlers.

        Returns
        -------
        list[str]
            Exception sort names that are in :attr:`exception_registry`
            but not in :attr:`handler_registry`.
        """
        return [s for s in self.exception_registry if s not in self.handler_registry]

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def disconnect(self) -> None:
        """Disconnect the bridge from the JuGeo infrastructure."""
        self.status = BridgeStatus.DISCONNECTED

    def reconnect(self) -> None:
        """Reconnect the bridge to the JuGeo infrastructure.

        If any exception sorts are unhandled, the bridge transitions to
        DEGRADED rather than CONNECTED.
        """
        if self.all_unhandled():
            self.status = BridgeStatus.DEGRADED
        else:
            self.status = BridgeStatus.CONNECTED

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def status_report(self) -> dict[str, Any]:
        """Return a status report for this bridge.

        Returns
        -------
        dict[str, Any]
            Keys: bridge_id, status, registered_exceptions, registered_handlers,
            unhandled_count, unhandled_sorts, reported_at.
        """
        unhandled = self.all_unhandled()
        return {
            "bridge_id": self.bridge_id,
            "status": self.status.value,
            "registered_exceptions": len(self.exception_registry),
            "registered_handlers": len(self.handler_registry),
            "unhandled_count": len(unhandled),
            "unhandled_sorts": unhandled,
            "all_exception_sorts": list(self.exception_registry.keys()),
            "all_handled_sorts": list(self.handler_registry.keys()),
            "reported_at": time.time(),
        }

    def __repr__(self) -> str:
        return (
            f"ExceptionSemanticsBridge("
            f"id={self.bridge_id[:8]}..., "
            f"status={self.status.value}, "
            f"exceptions={len(self.exception_registry)}, "
            f"handlers={len(self.handler_registry)})"
        )


# ---------------------------------------------------------------------------
# CopilotReconstructionAssist
# ---------------------------------------------------------------------------


@dataclass
class CopilotReconstructionAssist:
    """Copilot integration hook for partiality model reconstruction hints.

    Provides completion suggestions, explanations, and trace reconstruction
    to assist developers working with Ch31 partiality encodings.

    This class is designed as a lightweight "hint engine" that can be queried
    during interactive development or documentation generation.

    Attributes
    ----------
    assist_id:
        Unique identifier for this assist instance.
    hints:
        Pre-loaded hint strings keyed by variable or sort name.
    completions:
        Cache of completion suggestions already generated.
    confidence_scores:
        Confidence level [0.0, 1.0] for each completion or hint.
    """

    assist_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    hints: dict[str, str] = field(default_factory=dict)
    completions: dict[str, Any] = field(default_factory=dict)
    confidence_scores: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Completion suggestions
    # ------------------------------------------------------------------

    def suggest_completion(
        self, var_name: str, sort: str, context: dict[str, Any]
    ) -> str:
        """Suggest a default SMT2 value for an undefined variable.

        The suggestion is based on the sort of the variable:
        - ``Bool``   -> ``"false"``
        - ``Int``    -> ``"0"``
        - ``String`` -> ``'""'``
        - other      -> ``"(default-{sort})"``

        If a hint is already registered for *var_name*, it takes precedence.

        Parameters
        ----------
        var_name:
            The name of the variable needing a completion.
        sort:
            The SMT2 sort of the variable.
        context:
            Contextual information about the encoding (currently unused
            but reserved for future semantic-aware suggestions).

        Returns
        -------
        str
            An SMT2 expression string suitable as a default value.
        """
        # Check for a pre-registered hint first (highest confidence)
        if var_name in self.hints:
            completion = self.hints[var_name]
            self.completions[var_name] = completion
            self.confidence_scores[var_name] = 0.9
            return completion

        # Sort-based default selection
        if sort == "Bool":
            completion = "false"
        elif sort == "Int":
            completion = "0"
        elif sort == "Real":
            completion = "0.0"
        elif sort == "String":
            completion = '""'
        elif sort == "Array":
            completion = "((as const Array) 0)"
        else:
            completion = f"(default-{sort})"

        # Store completion and assign moderate confidence
        self.completions[var_name] = completion
        self.confidence_scores[var_name] = 0.5

        return completion

    # ------------------------------------------------------------------
    # Explanations
    # ------------------------------------------------------------------

    def explain_partiality(self, encoding_dict: dict[str, Any]) -> str:
        """Generate a human-readable explanation of why a function is partial.

        Inspects the encoding dict for ``"domain_pred"`` and ``"relation"``
        keys to produce a structured explanation.

        Parameters
        ----------
        encoding_dict:
            An encoding dictionary produced by
            :func:`~jugeo.encodings.partiality_model_reconstruction.algorithms.encode_partial_function`.

        Returns
        -------
        str
            A multi-line explanation string.
        """
        domain_pred = encoding_dict.get("domain_pred", "<unknown_dom>")
        relation = encoding_dict.get("relation", "<unknown_rel>")
        metadata = encoding_dict.get("metadata", {})
        name = metadata.get("name", encoding_dict.get("name", "<unknown>"))
        domain_sort = metadata.get("domain_sort", "?")
        range_sort = metadata.get("range_sort", "?")
        guard_expr = metadata.get("guard_expr", "<no guard>")

        lines = [
            f"Function '{name}' is partial because its domain is restricted by '{domain_pred}'.",
            f"",
            f"Type:          {name} : {domain_sort} ⇀ {range_sort}",
            f"Domain pred:   ({domain_pred} x)  — guards where the function is defined",
            f"Relation:      {relation}          — the underlying total Z3 function",
            f"Guard:         {guard_expr}",
            f"",
            f"Values of '{name}' outside the domain (where {domain_pred} is false)",
            f"are undefined.  To obtain a total function, use totalize_partial() to",
            f"supply a default value for the undefined cases.",
            f"",
            f"Ch31 §31.1 encoding identity:",
            f"  ∀ x : {domain_sort}. ({domain_pred} x) ⟹ ({relation} x) satisfies the guard",
        ]
        return "\n".join(lines)

    def suggest_totalization(self, domain_pred: str, range_sort: str) -> str:
        """Suggest a totalization expression for a partial function.

        Returns an SMT2 ``ite`` expression that totalizes the partial
        function ``f`` by providing a sort-appropriate default value.

        Parameters
        ----------
        domain_pred:
            The name of the domain predicate function.
        range_sort:
            The SMT2 sort of the function's range.

        Returns
        -------
        str
            An SMT2 ``ite`` expression string.
        """
        # Choose a sensible default based on range sort
        if range_sort == "Int":
            default = "0"
        elif range_sort == "Bool":
            default = "false"
        elif range_sort == "Real":
            default = "0.0"
        elif range_sort == "String":
            default = '""'
        else:
            default = "(default-value)"

        return f"(ite ({domain_pred} x) (f x) {default})"

    # ------------------------------------------------------------------
    # Trace reconstruction
    # ------------------------------------------------------------------

    def reconstruct_trace(
        self, exception_trace: list[str]
    ) -> dict[str, Any]:
        """Reconstruct a structured trace from a list of exception sort names.

        Each sort in the trace is treated as one propagation step.
        The first sort is the exception origin ("raised"); subsequent
        sorts are propagation points ("propagated").

        Parameters
        ----------
        exception_trace:
            Ordered list of exception sort names representing the
            propagation chain.

        Returns
        -------
        dict[str, Any]
            - ``trace_id``  : fresh UUID for this trace
            - ``steps``     : dict mapping step index to step descriptor
            - ``depth``     : number of propagation steps
            - ``entry``     : the first exception sort (or None)
        """
        trace_id = str(uuid.uuid4())

        trace_steps: dict[int, dict[str, Any]] = {}
        for i, sort in enumerate(exception_trace):
            action = "raised" if i == 0 else "propagated"
            trace_steps[i] = {
                "step": i,
                "sort": sort,
                "action": action,
                "depth": i,
                "is_entry": (i == 0),
                "is_terminal": (i == len(exception_trace) - 1),
            }

        return {
            "trace_id": trace_id,
            "steps": trace_steps,
            "depth": len(exception_trace),
            "entry": exception_trace[0] if exception_trace else None,
            "terminal": exception_trace[-1] if exception_trace else None,
            "reconstructed_at": time.time(),
        }

    # ------------------------------------------------------------------
    # Confidence and hint management
    # ------------------------------------------------------------------

    def confidence_for(self, key: str) -> float:
        """Return the confidence score for a given key.

        Parameters
        ----------
        key:
            The variable name or hint key to look up.

        Returns
        -------
        float
            The confidence score in [0.0, 1.0], or 0.0 if not known.
        """
        return self.confidence_scores.get(key, 0.0)

    def add_hint(self, key: str, hint: str) -> None:
        """Add or replace a hint for the given key with high confidence.

        Parameters
        ----------
        key:
            The variable or sort name to associate with the hint.
        hint:
            The hint string value.
        """
        self.hints[key] = hint
        # Pre-loaded hints are assumed high-confidence
        self.confidence_scores[key] = 0.9

    def merge_with(
        self, other: CopilotReconstructionAssist
    ) -> CopilotReconstructionAssist:
        """Produce a new assist by merging this instance with another.

        Merging rules:
        - Hints: self wins on conflict
        - Completions: self wins on conflict
        - Confidence scores: averaged when both have the same key

        Parameters
        ----------
        other:
            Another :class:`CopilotReconstructionAssist` to merge with.

        Returns
        -------
        CopilotReconstructionAssist
            A fresh instance with the merged state.
        """
        # Hints: self takes priority
        merged_hints = dict(other.hints)
        merged_hints.update(self.hints)

        # Completions: self takes priority
        merged_completions = dict(other.completions)
        merged_completions.update(self.completions)

        # Confidence: average where both have values; otherwise keep non-None
        all_keys = set(self.confidence_scores) | set(other.confidence_scores)
        merged_confidence: dict[str, float] = {}
        for k in all_keys:
            self_score = self.confidence_scores.get(k)
            other_score = other.confidence_scores.get(k)
            if self_score is not None and other_score is not None:
                merged_confidence[k] = (self_score + other_score) / 2.0
            elif self_score is not None:
                merged_confidence[k] = self_score
            else:
                merged_confidence[k] = other_score  # type: ignore[assignment]

        return CopilotReconstructionAssist(
            hints=merged_hints,
            completions=merged_completions,
            confidence_scores=merged_confidence,
        )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a summary dict for this assist instance.

        Returns
        -------
        dict[str, Any]
            Keys: assist_id, hint_count, completion_count, avg_confidence.
        """
        scores = list(self.confidence_scores.values())
        avg_confidence = sum(scores) / max(len(scores), 1)

        return {
            "assist_id": self.assist_id,
            "hint_count": len(self.hints),
            "completion_count": len(self.completions),
            "avg_confidence": avg_confidence,
            "all_hint_keys": list(self.hints.keys()),
            "all_completion_keys": list(self.completions.keys()),
        }

    def __repr__(self) -> str:
        return (
            f"CopilotReconstructionAssist("
            f"id={self.assist_id[:8]}..., "
            f"hints={len(self.hints)}, "
            f"completions={len(self.completions)})"
        )


# ---------------------------------------------------------------------------
# Module-level factory functions
# ---------------------------------------------------------------------------


def create_encoding_session() -> PartialityEncodingSession:
    """Create a fresh :class:`PartialityEncodingSession`.

    Returns
    -------
    PartialityEncodingSession
        A new session in the IDLE state.
    """
    return PartialityEncodingSession()


def create_reconstruction_pipeline(
    session: PartialityEncodingSession,
) -> ModelReconstructionPipeline:
    """Create a :class:`ModelReconstructionPipeline` bound to an existing session.

    Parameters
    ----------
    session:
        The encoding session to bind to the pipeline.

    Returns
    -------
    ModelReconstructionPipeline
        A new pipeline wrapping the given session.
    """
    return ModelReconstructionPipeline(session=session)


def create_exception_bridge() -> ExceptionSemanticsBridge:
    """Create a fresh :class:`ExceptionSemanticsBridge`.

    Returns
    -------
    ExceptionSemanticsBridge
        A new bridge in the CONNECTED state with empty registries.
    """
    return ExceptionSemanticsBridge()


def create_copilot_assist() -> CopilotReconstructionAssist:
    """Create a fresh :class:`CopilotReconstructionAssist`.

    Returns
    -------
    CopilotReconstructionAssist
        A new assist instance with empty hint and completion caches.
    """
    return CopilotReconstructionAssist()


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "SessionState",
    "BridgeStatus",
    # Dataclasses
    "PartialityEncodingSession",
    "ModelReconstructionPipeline",
    "ExceptionSemanticsBridge",
    "CopilotReconstructionAssist",
    # Factory functions
    "create_encoding_session",
    "create_reconstruction_pipeline",
    "create_exception_bridge",
    "create_copilot_assist",
]
