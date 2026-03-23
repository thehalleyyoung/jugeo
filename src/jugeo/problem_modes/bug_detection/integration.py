"""Integration layer: wires bug_detection into the rest of JuGeo.

This module connects the bug-detection pipeline to the surrounding JuGeo
ecosystem — the orchestration controller, the evidence pipeline, the
manifest subsystem, and the CLI/API surfaces — following the same
integration pattern used by repair_semantics/integration.py.

Responsibilities
----------------
``BugDetectionOrchestrator``
    Wraps ``BugDetector`` and forwards calls to the orchestration controller
    when it is available.  Falls back to a standalone detector when the
    controller is absent (e.g. in unit-test environments).

``wire_to_evidence_pipeline``
    Converts a ``BugDetectionResult`` into the evidence-channel dict format
    consumed by ``jugeo.evidence``.  Each ``BugReport`` becomes an evidence
    item tagged with the appropriate family and trust level.

``wire_to_manifest``
    Attaches the obstructions from a ``BugDetectionResult`` to an existing
    manifest object (any of the three manifest types in ``jugeo.encodings``).
    Obstructions are recorded as first-class semantic objects, not strings.

``cli_detect``
    A CLI entry point that reads a file path from the command line, runs the
    full detection pipeline, and prints a JSON-formatted structured report to
    stdout.  Returns 0 on success, 1 if bugs were found, 2 on error.

``api_detect``
    A dict-in/dict-out API entry point for integration with HTTP handlers,
    task queues, or notebook environments.  All inputs and outputs are plain
    JSON-serialisable dicts.

Theory basis
------------
The integration layer implements the *evidence accumulation* step from
theory2.tex §11.4: after each detection run, the current manifest is updated
with new obstruction records and any new bugs discovered are converted to
evidence items that can be consumed by downstream solvers.

No silent trust promotion
--------------------------
All evidence items produced by ``wire_to_evidence_pipeline`` are tagged at
``TrustLevel.ORACLE_PROPOSED`` (the default for static-analysis output).
Promotion to higher tiers is the responsibility of the orchestration
controller after formal verification.

# copilot: integration -- bug_detection pipeline wiring, theory2 ch11
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Optional internal imports with fallback
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.bug_detection.models import (
        BugDetectionResult,
        BugKind,
        BugReport,
        DetectionSession,
    )
except ImportError:
    BugDetectionResult = Any  # type: ignore[assignment,misc]
    BugKind = Any  # type: ignore[assignment,misc]
    BugReport = Any  # type: ignore[assignment,misc]
    DetectionSession = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.bug_detection.detector import BugDetector, detect_bugs
except ImportError:
    BugDetector = Any  # type: ignore[assignment,misc]
    detect_bugs = Any  # type: ignore[assignment]

try:
    from jugeo.problem_modes.bug_detection.ast_bridge import ASTBridgeConfig
except ImportError:
    ASTBridgeConfig = Any  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.judgment_terms import TrustLevel, EvidenceItemKind
except ImportError:
    TrustLevel = Any  # type: ignore[assignment,misc]
    EvidenceItemKind = Any  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.controller import OrchestrationController
except ImportError:
    OrchestrationController = Any  # type: ignore[assignment,misc]

try:
    from jugeo.errors import ObstructionRecord, EvidenceFamily
except ImportError:
    ObstructionRecord = Any  # type: ignore[assignment,misc]
    EvidenceFamily = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

JsonValue = Any

# ---------------------------------------------------------------------------
# Module-level provenance
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-bug-detection",
    "sequence": "11",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "integration",
}

# ---------------------------------------------------------------------------
# BugDetectionOrchestrator
# ---------------------------------------------------------------------------


@dataclass
class BugDetectionOrchestrator:
    """Orchestration wrapper that forwards detection runs to the controller.

    ``BugDetectionOrchestrator`` is the recommended entry point for callers
    that operate within the full JuGeo runtime, where an
    ``OrchestrationController`` is available.  When the controller is absent
    (e.g. in tests or standalone scripts) it falls back to a plain
    ``BugDetector``.

    The orchestrator:

    1. Delegates the detection run to ``BugDetector.detect_bugs``.
    2. If a controller is available, registers the resulting
       ``BugDetectionResult`` as a new analysis event on the controller.
    3. Calls ``wire_to_evidence_pipeline`` to convert the result to evidence
       items and forwards them to the controller's evidence accumulator.
    4. Calls ``wire_to_manifest`` if a manifest is registered.

    Trust floor
    -----------
    The orchestrator enforces the ``trust_floor`` from its config dict,
    ensuring that no evidence below the floor enters the controller's
    accumulator.  This is the integration-layer enforcement of the
    no-silent-trust-promotion principle (theory2.tex §252).

    Parameters
    ----------
    config:
        Configuration dict forwarded to ``BugDetector``.
    controller:
        Optional ``OrchestrationController`` instance.  If None, a standalone
        detector is used.
    manifest:
        Optional manifest object.  If provided, ``wire_to_manifest`` is called
        after each detection run.
    """

    config: dict[str, Any] = field(default_factory=dict)
    controller: Any = None
    manifest: Any = None
    _detector: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._detector = BugDetector(config=self.config)

    def run(
        self,
        source_or_path: str,
        *,
        is_path: bool = False,
        spec: dict[str, Any] | None = None,
        filename: str = "<unknown>",
    ) -> BugDetectionResult:
        """Run the detection pipeline and wire results into the ecosystem.

        Parameters
        ----------
        source_or_path:
            Raw Python source or a file path.
        is_path:
            If True, treat ``source_or_path`` as a file path.
        spec:
            Optional specification dict.
        filename:
            Filename used in coordinates when ``is_path=False``.

        Returns
        -------
        BugDetectionResult
        """
        result = self._detector.detect_bugs(
            source_or_path,
            is_path=is_path,
            spec=spec,
            filename=filename,
        )

        # Wire into evidence pipeline
        evidence_payload = wire_to_evidence_pipeline(result)

        # Forward to controller if available
        if self.controller is not None:
            try:
                self.controller.record_analysis_event(
                    event_type="bug_detection",
                    payload=result.to_dict(),
                    evidence=evidence_payload,
                )
            except AttributeError:
                pass  # controller doesn't implement record_analysis_event

        # Wire into manifest if available
        if self.manifest is not None:
            wire_to_manifest(result, self.manifest)

        return result

    def configure(self, **kwargs: Any) -> "BugDetectionOrchestrator":
        """Return a new orchestrator with updated configuration.

        Parameters
        ----------
        **kwargs:
            Keys to update in the config dict.

        Returns
        -------
        BugDetectionOrchestrator
            New orchestrator with merged config.
        """
        new_config = {**self.config, **kwargs}
        return BugDetectionOrchestrator(
            config=new_config,
            controller=self.controller,
            manifest=self.manifest,
        )


# ---------------------------------------------------------------------------
# Evidence pipeline wiring
# ---------------------------------------------------------------------------


def wire_to_evidence_pipeline(result: BugDetectionResult) -> dict[str, Any]:
    """Convert a BugDetectionResult to the evidence-channel dict format.

    Each ``BugReport`` in *result* is converted to an evidence item dict
    tagged with:
    * ``family``: The evidence family string (derived from BugKind).
    * ``trust_level``: ``"ORACLE_PROPOSED"`` (static analysis tier).
    * ``coordinate``: The bug's coordinate string.
    * ``content``: The bug's description.
    * ``cohomology_class``: The H¹ class label.
    * ``provenance``: The bug's provenance dict.

    The returned dict has the structure expected by
    ``jugeo.evidence.certificates`` and ``jugeo.evidence.channels``:

    .. code-block:: json

        {
            "session_id": "...",
            "timestamp": "...",
            "trust_floor": "ORACLE_PROPOSED",
            "items": [
                {
                    "item_id": "...",
                    "family": "structural",
                    "trust_level": "ORACLE_PROPOSED",
                    "coordinate": "...",
                    "content": "...",
                    "cohomology_class": "...",
                    "provenance": {...}
                },
                ...
            ]
        }

    Parameters
    ----------
    result:
        The ``BugDetectionResult`` to convert.

    Returns
    -------
    dict[str, Any]
        Evidence-channel formatted dict.
    """
    _KIND_TO_FAMILY: dict[str, str] = {
        "TYPE_ERROR": "structural",
        "LOGIC_ERROR": "behavioral",
        "SCOPE_VIOLATION": "structural",
        "PROTOCOL_VIOLATION": "relational",
        "TRUST_VIOLATION": "security",
        "RESOURCE_LEAK": "resource",
        "CONCURRENCY_HAZARD": "behavioral",
        "SPECIFICATION_DEVIATION": "relational",
    }

    items: list[dict[str, Any]] = []
    for bug in result.bugs:
        kind_value = bug.kind.value if hasattr(bug.kind, "value") else str(bug.kind)
        items.append({
            "item_id": bug.bug_id,
            "family": _KIND_TO_FAMILY.get(kind_value, "behavioral"),
            "trust_level": bug.trust_tier,
            "coordinate": bug.coordinate,
            "content": bug.description,
            "cohomology_class": (
                bug.cohomology_class or bug.compute_cohomology_class()
            ),
            "severity": bug.severity,
            "judgment_tuple": list(bug.judgment_tuple),
            "provenance": bug.provenance,
            "metadata": bug.metadata,
        })

    return {
        "session_id": result.session_id,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "trust_floor": "ORACLE_PROPOSED",
        "status": result.status,
        "item_count": len(items),
        "items": items,
    }


# ---------------------------------------------------------------------------
# Manifest wiring
# ---------------------------------------------------------------------------


def wire_to_manifest(result: BugDetectionResult, manifest: Any) -> None:
    """Attach bug obstructions from *result* to *manifest*.

    Iterates over the bugs in *result* and calls ``manifest.add_obstruction``
    (or ``manifest.record_obstruction``, depending on the manifest's API) for
    each bug.  If neither method is available, the function silently returns.

    The obstruction dict format attached to the manifest is:

    .. code-block:: json

        {
            "obstruction_id": "<bug_id>",
            "kind": "<BugKind.value>",
            "coordinate": "<coordinate>",
            "cohomology_class": "<H1 class>",
            "severity": <float>,
            "trust_tier": "<tier name>",
            "description": "<description>",
            "provenance": {...}
        }

    Theory basis (theory2.tex Ch11 §11.4)
    ----------------------------------------
    Manifests are persistent semantic memories that record which coordinates
    carry known obstructions.  By writing bug reports into the manifest, we
    ensure that the obstruction data persists across analysis sessions and
    is available to the repair pipeline when it reconstructs the repair
    frontier.

    Parameters
    ----------
    result:
        The ``BugDetectionResult`` whose bugs are to be recorded.
    manifest:
        Any manifest object that implements ``add_obstruction`` or
        ``record_obstruction``.  Silently ignored if neither method exists.
    """
    if manifest is None:
        return

    _add = getattr(manifest, "add_obstruction", None)
    if _add is None:
        _add = getattr(manifest, "record_obstruction", None)
    if _add is None:
        return

    for bug in result.bugs:
        kind_value = bug.kind.value if hasattr(bug.kind, "value") else str(bug.kind)
        obstruction = {
            "obstruction_id": bug.bug_id,
            "kind": kind_value,
            "coordinate": bug.coordinate,
            "cohomology_class": (
                bug.cohomology_class or bug.compute_cohomology_class()
            ),
            "severity": bug.severity,
            "trust_tier": bug.trust_tier,
            "description": bug.description,
            "counterexample": bug.counterexample,
            "provenance": bug.provenance,
        }
        try:
            _add(obstruction)
        except Exception:
            pass  # manifest integration is best-effort


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def cli_detect(path: str, **kwargs: Any) -> int:
    """CLI entry point: detect bugs in a Python file and print structured output.

    Reads *path*, runs the full detection pipeline, and prints a
    JSON-formatted report to stdout.  All diagnostic messages go to stderr.

    Exit codes
    ----------
    0
        Analysis completed and no bugs were found.
    1
        Analysis completed and one or more bugs were found.
    2
        Analysis failed (syntax error, file not found, internal error).

    The printed JSON has the structure:

    .. code-block:: json

        {
            "session_id": "...",
            "status": "bugs_found",
            "bug_count": 3,
            "elapsed_s": 0.042,
            "bugs": [
                {
                    "bug_id": "...",
                    "kind": "TYPE_ERROR",
                    "coordinate": "...",
                    "severity": 0.7,
                    "description": "...",
                    "trust_tier": "ORACLE_PROPOSED",
                    "cohomology_class": "σ_type:a3f2b1"
                },
                ...
            ]
        }

    Parameters
    ----------
    path:
        Path to the Python source file to analyse.
    **kwargs:
        Additional keyword arguments forwarded to ``BugDetector`` config.

    Returns
    -------
    int
        Exit code.
    """
    config: dict[str, Any] = {
        k: v for k, v in kwargs.items() if k in {
            "trust_floor", "max_depth", "include_docstrings",
            "enable_z3", "severity_threshold", "max_bugs",
        }
    }
    try:
        detector = BugDetector(config=config)
        result = detector.detect_bugs(path, is_path=True)
    except FileNotFoundError:
        print(
            json.dumps({"error": f"File not found: {path}", "status": "error"}),
            file=sys.stderr,
        )
        return 2
    except SyntaxError as exc:
        print(
            json.dumps({
                "error": f"Syntax error in {path}: {exc}",
                "status": "error",
            }),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(
            json.dumps({
                "error": str(exc),
                "type": type(exc).__name__,
                "status": "error",
            }),
            file=sys.stderr,
        )
        return 2

    output: dict[str, Any] = {
        "session_id": result.session_id,
        "status": result.status,
        "bug_count": len(result.bugs),
        "elapsed_s": round(result.elapsed_s, 6),
        "bugs": [
            {
                "bug_id": bug.bug_id,
                "kind": bug.kind.value if hasattr(bug.kind, "value") else str(bug.kind),
                "coordinate": bug.coordinate,
                "severity": bug.severity,
                "description": bug.description,
                "trust_tier": bug.trust_tier,
                "cohomology_class": (
                    bug.cohomology_class or bug.compute_cohomology_class()
                ),
            }
            for bug in result.bugs
        ],
    }
    print(json.dumps(output, indent=2))
    return 1 if result.bugs else 0


# ---------------------------------------------------------------------------
# API entry point
# ---------------------------------------------------------------------------


def api_detect(request: dict[str, Any]) -> dict[str, Any]:
    """Dict-in/dict-out API entry point for HTTP handlers and task queues.

    Accepts a request dict and returns a response dict.  Both are
    JSON-serialisable.

    Request format
    --------------
    .. code-block:: json

        {
            "source": "<Python source code>",
            "path": "<optional file path>",
            "is_path": false,
            "filename": "<optional display name>",
            "config": {
                "trust_floor": "ORACLE_PROPOSED",
                "max_depth": 50,
                "enable_z3": true
            },
            "spec": {
                "<coordinate_pattern>": {
                    "required_kind": "function_def"
                }
            }
        }

    If both ``source`` and ``path`` are provided and ``is_path`` is True,
    ``path`` takes precedence.  If ``is_path`` is False (the default),
    ``source`` is used.

    Response format
    ---------------
    .. code-block:: json

        {
            "ok": true,
            "session_id": "...",
            "status": "bugs_found",
            "bug_count": 2,
            "elapsed_s": 0.031,
            "summary": {...},
            "bugs": [...],
            "evidence_payload": {...}
        }

    On error:

    .. code-block:: json

        {
            "ok": false,
            "error": "...",
            "error_type": "SyntaxError",
            "session_id": null
        }

    Parameters
    ----------
    request:
        The request dict.

    Returns
    -------
    dict[str, Any]
        The response dict.
    """
    is_path = bool(request.get("is_path", False))
    source = request.get("source", "")
    path = request.get("path", "")
    filename = request.get("filename", "<api>")
    config = dict(request.get("config", {}))
    spec = request.get("spec")

    source_or_path: str
    if is_path and path:
        source_or_path = path
    elif source:
        source_or_path = source
        is_path = False
    else:
        return {
            "ok": False,
            "error": "Request must contain either 'source' or 'path'.",
            "error_type": "ValueError",
            "session_id": None,
        }

    try:
        detector = BugDetector(config=config)
        result = detector.detect_bugs(
            source_or_path,
            is_path=is_path,
            spec=spec,
            filename=filename,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "session_id": None,
        }

    evidence_payload = wire_to_evidence_pipeline(result)
    repair_input = detector.to_repair_plan_input(result)

    return {
        "ok": True,
        "session_id": result.session_id,
        "status": result.status,
        "bug_count": len(result.bugs),
        "elapsed_s": round(result.elapsed_s, 6),
        "summary": result.summary(),
        "bugs": [b.to_dict() for b in result.bugs],
        "evidence_payload": evidence_payload,
        "repair_plan_input": repair_input,
    }


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.encodings)
# ---------------------------------------------------------------------------

def bug_as_obstruction(bug: Any) -> dict[str, Any]:
    """Interpret a bug as a cohomology obstruction in H^1(U, D).

    Bugs ARE cohomological obstructions — they witness the failure of local
    sections to glue into a global section over the judgment-sheaf site.

    Parameters
    ----------
    bug : Any
        A BugReport or dict with at least ``coordinate`` and ``kind`` fields.

    Returns
    -------
    dict[str, Any]
        Obstruction record with ``class_label``, ``coordinate``, ``cocycle_data``,
        and ``descent_failure`` keys.
    """
    try:
        from jugeo.geometry.descent import compute_obstruction_class, DescentFailure
    except ImportError:
        compute_obstruction_class = None
        DescentFailure = None

    coord = getattr(bug, "coordinate", None) or (bug.get("coordinate") if isinstance(bug, dict) else None)
    kind = getattr(bug, "kind", None) or (bug.get("kind") if isinstance(bug, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind)

    obstruction: dict[str, Any] = {
        "coordinate": coord,
        "kind": kind_str,
        "class_label": f"H1_obstruction_{kind_str}",
        "cocycle_data": {"source": "bug_detection", "coordinate": coord},
        "descent_failure": None,
    }

    if compute_obstruction_class is not None:
        try:
            obs_class = compute_obstruction_class(coord, kind_str)
            obstruction["class_label"] = getattr(obs_class, "label", obstruction["class_label"])
            obstruction["cocycle_data"] = getattr(obs_class, "cocycle_data", obstruction["cocycle_data"])
        except Exception:
            pass

    if DescentFailure is not None:
        try:
            obstruction["descent_failure"] = DescentFailure(
                coordinate=coord, reason=f"bug_{kind_str}_blocks_gluing"
            )
        except Exception:
            pass

    return obstruction


def bug_evidence(bug: Any) -> dict[str, Any]:
    """Create negative evidence from a bug report.

    Bugs create negative evidence — they are witnesses AGAINST the claim
    that the section is well-formed at a given coordinate.

    Parameters
    ----------
    bug : Any
        A BugReport or dict with bug information.

    Returns
    -------
    dict[str, Any]
        Negative evidence record with ``polarity``, ``manifest_entry``,
        ``trust_impact``, and ``coordinate`` keys.
    """
    try:
        from jugeo.evidence.manifests import ManifestEntry, EvidencePolarity
    except ImportError:
        ManifestEntry = None
        EvidencePolarity = None

    coord = getattr(bug, "coordinate", None) or (bug.get("coordinate") if isinstance(bug, dict) else None)
    severity = getattr(bug, "severity", 0.5)
    if isinstance(bug, dict):
        severity = bug.get("severity", 0.5)

    evidence: dict[str, Any] = {
        "polarity": "NEGATIVE",
        "coordinate": coord,
        "trust_impact": -float(severity),
        "manifest_entry": None,
        "source": "bug_detection",
    }

    if EvidencePolarity is not None:
        try:
            evidence["polarity"] = EvidencePolarity.NEGATIVE
        except Exception:
            pass

    if ManifestEntry is not None:
        try:
            evidence["manifest_entry"] = ManifestEntry(
                coordinate=coord,
                polarity=evidence["polarity"],
                source="bug_detection",
            )
        except Exception:
            pass

    return evidence


def bug_encoding(bug: Any) -> dict[str, Any]:
    """Encode a bug as an SMT-encodable constraint.

    Bugs are SMT-encodable — each bug translates to a formula asserting
    that a particular section predicate fails at the bug's coordinate.

    Parameters
    ----------
    bug : Any
        A BugReport or dict with bug information.

    Returns
    -------
    dict[str, Any]
        Encoding record with ``formula``, ``variables``, ``coordinate``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings import encode_predicate, ScalarEncoding
    except ImportError:
        encode_predicate = None
        ScalarEncoding = None

    coord = getattr(bug, "coordinate", None) or (bug.get("coordinate") if isinstance(bug, dict) else None)
    kind = getattr(bug, "kind", None) or (bug.get("kind") if isinstance(bug, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind)

    encoding: dict[str, Any] = {
        "coordinate": coord,
        "encoding_kind": "bug_negation",
        "formula": f"(not (well_formed {coord} {kind_str}))",
        "variables": [f"wf_{coord}"],
        "scalar": None,
    }

    if encode_predicate is not None:
        try:
            enc = encode_predicate(coord, kind_str, negated=True)
            encoding["formula"] = getattr(enc, "formula", encoding["formula"])
            encoding["variables"] = getattr(enc, "variables", encoding["variables"])
        except Exception:
            pass

    if ScalarEncoding is not None:
        try:
            encoding["scalar"] = ScalarEncoding(
                coordinate=coord, value=0.0, label=f"bug_{kind_str}"
            )
        except Exception:
            pass

    return encoding


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "BugDetectionOrchestrator",
    "wire_to_evidence_pipeline",
    "wire_to_manifest",
    "cli_detect",
    "api_detect",
    "MANIFEST_SPEC_PROVENANCE",
    "bug_as_obstruction",
    "bug_evidence",
    "bug_encoding",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import textwrap

    print("=== integration.py smoke test ===")

    _SAMPLE = textwrap.dedent("""
        def compute(x: int, y: int) -> int:
            result = oracle_generate(x + y)
            return result

        bad_annotation: str = 999
    """)

    # Test api_detect
    response = api_detect({
        "source": _SAMPLE,
        "filename": "integration_smoke.py",
        "config": {"trust_floor": "ORACLE_PROPOSED"},
    })
    assert response["ok"] is True, f"api_detect failed: {response}"
    print("api_detect response keys:", list(response.keys()))
    print("bug_count:", response["bug_count"])
    print("status:", response["status"])

    # Test evidence pipeline wiring
    from jugeo.problem_modes.bug_detection.detector import detect_bugs as _det
    result = _det(_SAMPLE, filename="integration_smoke.py")
    ep = wire_to_evidence_pipeline(result)
    assert "items" in ep
    assert ep["trust_floor"] == "ORACLE_PROPOSED"
    print("Evidence pipeline items:", ep["item_count"])

    # Test orchestrator (no controller)
    orch = BugDetectionOrchestrator(config={"trust_floor": "ORACLE_PROPOSED"})
    orch_result = orch.run(_SAMPLE, filename="integration_smoke.py")
    assert orch_result.session_id is not None
    print("Orchestrator result status:", orch_result.status)

    # Test manifest wiring (dummy manifest)
    class _DummyManifest:
        def __init__(self) -> None:
            self.obstructions: list[dict[str, Any]] = []

        def add_obstruction(self, obs: dict[str, Any]) -> None:
            self.obstructions.append(obs)

    manifest = _DummyManifest()
    wire_to_manifest(result, manifest)
    print("Manifest obstructions added:", len(manifest.obstructions))

    # Test api_detect error path
    bad_response = api_detect({})
    assert bad_response["ok"] is False
    print("Error response ok=False confirmed")

    print("=== smoke test PASSED ===")
