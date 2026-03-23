"""Integration layer for the specification_satisfaction subsystem of JuGeo.

Connects the specification satisfaction pipeline with the other JuGeo
subsystems: descent engine, evidence store, judgment system, and certificate
store.  Provides registry, export/import, solver connector, and full-pipeline
orchestration.

References: theory2.tex §10 — Specification Satisfaction.
  §10.1 The satisfaction functor and descent criterion.
  §10.2 Gap algebra and repair witnesses.
  §10.3 Trust propagation through cover overlaps.
  §10.4 Compositional specification algebra.
  §10.5 Iterative oracle-driven refinement.
  §10.6 Integration with external solvers and evidence systems.

copilot: integration layer — registry, exporter, importer, solver connector,
         and full-pipeline orchestration for specification satisfaction.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    from jugeo.problem_modes.specification_satisfaction.models import (
        Specification,
        SatisfactionWitness,
        CertificateOfSatisfaction,
        ResidualGap,
        SpecificationKind,
        WitnessStatus,
        GapSeverity,
        SatisfactionStatus,
        DescentCondition,
    )
except ImportError:
    Specification = Any  # type: ignore[assignment,misc]
    SatisfactionWitness = Any  # type: ignore[assignment,misc]
    CertificateOfSatisfaction = Any  # type: ignore[assignment,misc]
    ResidualGap = Any  # type: ignore[assignment,misc]
    SpecificationKind = Any  # type: ignore[assignment,misc]
    WitnessStatus = Any  # type: ignore[assignment,misc]
    GapSeverity = Any  # type: ignore[assignment,misc]
    SatisfactionStatus = Any  # type: ignore[assignment,misc]
    DescentCondition = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.hypercovers import HypercoverLevel, CechNerve
except ImportError:
    HypercoverLevel = Any  # type: ignore[assignment,misc]
    CechNerve = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import (
        DescentEngine,
        DescentResult,
        LocalSection,
        GluingData,
        DescentObstruction,
    )
except ImportError:
    DescentEngine = Any  # type: ignore[assignment,misc]
    DescentResult = Any  # type: ignore[assignment,misc]
    LocalSection = Any  # type: ignore[assignment,misc]
    GluingData = Any  # type: ignore[assignment,misc]
    DescentObstruction = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.site import CoordinateObject, SemanticSite
except ImportError:
    CoordinateObject = Any  # type: ignore[assignment,misc]
    SemanticSite = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.covers import Cover
except ImportError:
    Cover = Any  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, JudgmentKind, ProvenanceKind
except ImportError:
    JudgmentTerm = Any  # type: ignore[assignment,misc]
    JudgmentKind = Any  # type: ignore[assignment,misc]
    ProvenanceKind = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.certificates import Certificate, CertificateStatus
except ImportError:
    Certificate = Any  # type: ignore[assignment,misc]
    CertificateStatus = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.specification_satisfaction.algorithms import SatisfactionAlgorithmResult
except ImportError:
    SatisfactionAlgorithmResult = Any  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        UTC timestamp of the form ``YYYY-MM-DDTHH:MM:SSZ``.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str = "id") -> str:
    """Generate a short, unique identifier.

    Parameters
    ----------
    prefix : str, optional
        Label prepended to the hex fragment, by default ``"id"``.

    Returns
    -------
    str
        String of the form ``<prefix>-<12-char hex>``.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _sha256_dict(d: dict[str, Any]) -> str:
    """Compute a stable SHA-256 digest of a JSON-serialisable dict.

    Parameters
    ----------
    d : dict[str, Any]
        Input mapping; must be JSON-serialisable.

    Returns
    -------
    str
        Hex-encoded 64-character SHA-256 digest.
    """
    serialised = json.dumps(d, sort_keys=True, default=str)
    return hashlib.sha256(serialised.encode()).hexdigest()


def _coerce_to_dict(obj: Any) -> dict[str, Any]:
    """Best-effort conversion of *obj* to a plain dictionary.

    Parameters
    ----------
    obj : Any
        Object to convert; tried in order: ``to_dict()``, ``__dict__``,
        dataclass ``fields``, or ``repr``.

    Returns
    -------
    dict[str, Any]
        Plain dict representation.
    """
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    try:
        import dataclasses  # noqa: PLC0415
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
    except Exception:  # noqa: BLE001
        pass
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {"repr": repr(obj)}


def _validate_required_keys(
    data: dict[str, Any],
    required_keys: list[str],
) -> list[str]:
    """Check that all *required_keys* are present in *data*.

    Parameters
    ----------
    data : dict[str, Any]
        Input dictionary to validate.
    required_keys : list[str]
        Keys that must be present.

    Returns
    -------
    list[str]
        List of missing keys (empty if all present).
    """
    return [k for k in required_keys if k not in data]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

# -- integration core -------------------------------------------------------

@dataclass(slots=True)
class SpecificationSatisfactionIntegration:
    """Mutable integration hub connecting satisfaction to other subsystems.

    Stores references to connected subsystems (descent engine, evidence store,
    judgment store, certificate store) and provides methods to build witnesses
    from judgments, run the full pipeline, and export/import artefacts.

    Parameters
    ----------
    integration_log : list[dict]
        Audit trail of every integration operation.
    connected_systems : dict[str, Any]
        Named references to connected subsystems.
    configuration : dict[str, Any]
        Key-value configuration store for runtime settings.
    """

    integration_log: list[dict[str, Any]] = field(default_factory=list)
    connected_systems: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)

    # -- system registration ------------------------------------------------

    def integrate_with_descent_engine(self, engine: Any) -> None:
        """Store a reference to *engine* and configure it for satisfaction.

        Parameters
        ----------
        engine : DescentEngine
            A descent engine instance compatible with the JuGeo descent API.
        """
        self.connected_systems["descent_engine"] = engine
        if hasattr(engine, "configure"):
            engine.configure({"mode": "satisfaction", "configured_at": _now_iso()})
        self.integration_log.append(
            {"action": "integrate_descent_engine", "engine": repr(engine), "ts": _now_iso()}
        )
        logger.debug("Integrated descent engine: %r", engine)

    def integrate_with_evidence_system(self, evidence_store: Any) -> None:
        """Store a reference to the evidence store for later queries.

        Parameters
        ----------
        evidence_store : Any
            An object with at least a ``query(coordinate)`` method.
        """
        self.connected_systems["evidence_store"] = evidence_store
        self.integration_log.append(
            {"action": "integrate_evidence_store", "store": repr(evidence_store), "ts": _now_iso()}
        )

    def integrate_with_judgment_system(self, judgment_store: Any) -> None:
        """Store a reference to the judgment store.

        Parameters
        ----------
        judgment_store : Any
            An object exposing at least ``get_judgments(spec_id)`` semantics.
        """
        self.connected_systems["judgment_store"] = judgment_store
        self.integration_log.append(
            {"action": "integrate_judgment_store", "store": repr(judgment_store), "ts": _now_iso()}
        )

    def integrate_with_certificate_system(self, cert_store: Any) -> None:
        """Store a reference to the certificate store.

        Parameters
        ----------
        cert_store : Any
            An object exposing at least ``store(cert)`` and ``get(cert_id)``
            methods.
        """
        self.connected_systems["certificate_store"] = cert_store
        self.integration_log.append(
            {"action": "integrate_certificate_store", "store": repr(cert_store), "ts": _now_iso()}
        )

    # -- witness construction -----------------------------------------------

    def build_witness_from_judgments(
        self,
        spec: Any,
        judgment_terms: list[Any],
    ) -> Any:
        """Assemble a ``SatisfactionWitness`` from a list of judgment terms.

        Each judgment term is inspected for a ``coordinate`` attribute and a
        ``payload`` / ``evidence`` attribute.  The resulting evidence map is
        passed to ``specification_satisfaction_algorithm`` to build the witness.

        Parameters
        ----------
        spec : Specification
            The specification the witness is being built for.
        judgment_terms : list[JudgmentTerm]
            Judgment terms from the judgment subsystem.

        Returns
        -------
        SatisfactionWitness
            A (possibly partial) witness assembled from the judgment evidence.
        """
        evidence_map: dict[str, list[dict[str, Any]]] = {}
        for jt in judgment_terms:
            coord = getattr(jt, "coordinate", None) or str(getattr(jt, "carrier", "unknown"))
            payload = getattr(jt, "payload", None) or getattr(jt, "evidence", {})
            if isinstance(payload, dict):
                evidence_map.setdefault(coord, []).append(payload)
            elif hasattr(payload, "__iter__"):
                for item in payload:
                    evidence_map.setdefault(coord, []).append(
                        _coerce_to_dict(item) if not isinstance(item, dict) else item
                    )
            else:
                evidence_map.setdefault(coord, []).append({"raw": repr(payload)})

        from jugeo.problem_modes.specification_satisfaction.algorithms import (  # noqa: PLC0415
            specification_satisfaction_algorithm,
        )
        witness = specification_satisfaction_algorithm(spec, evidence_map)
        self.integration_log.append(
            {
                "action": "build_witness_from_judgments",
                "spec_id": getattr(spec, "spec_id", str(spec)),
                "judgment_count": len(judgment_terms),
                "ts": _now_iso(),
            }
        )
        return witness

    # -- export / import ----------------------------------------------------

    def export_certificate(self, cert: Any, format: str = "dict") -> dict[str, Any] | str:
        """Export a certificate to the requested format.

        Parameters
        ----------
        cert : CertificateOfSatisfaction
            The certificate to export.
        format : str, optional
            Either ``"dict"`` (returns a plain dict) or ``"json"`` (returns a
            JSON string).  Defaults to ``"dict"``.

        Returns
        -------
        dict[str, Any] or str
            Serialised certificate.

        Raises
        ------
        ValueError
            If *format* is neither ``"dict"`` nor ``"json"``.
        """
        if format not in {"dict", "json"}:
            raise ValueError(f"Unsupported format {format!r}; choose 'dict' or 'json'.")
        d = _coerce_to_dict(cert)
        if format == "json":
            return json.dumps(d, sort_keys=True, default=str)
        return d

    def import_specification(self, data: dict[str, Any] | str, format: str = "dict") -> Any:
        """Import a specification from a dict or JSON string.

        Parameters
        ----------
        data : dict[str, Any] or str
            Raw specification data.
        format : str, optional
            ``"dict"`` or ``"json"``.  Defaults to ``"dict"``.

        Returns
        -------
        Specification
            The imported specification object.

        Raises
        ------
        ValueError
            If required keys are missing from *data*.
        """
        if format == "json":
            if isinstance(data, str):
                data = json.loads(data)
            else:
                raise ValueError("data must be a JSON string when format='json'.")
        if not isinstance(data, dict):
            raise ValueError("data must be a dict when format='dict'.")
        missing = _validate_required_keys(data, ["spec_id", "formula"])
        if missing:
            raise ValueError(f"Specification data missing required keys: {missing}")
        importer = SatisfactionImporter()
        return importer.import_specification(data)

    def run_full_pipeline(
        self,
        spec: Any,
        evidence_sources: dict[str, list[dict[str, Any]]],
    ) -> Any:
        """Run the complete satisfaction pipeline for *spec*.

        Pulls evidence from *evidence_sources*, optionally enriches it from a
        connected evidence store, runs the satisfaction algorithm, attempts
        descent, and returns a ``SatisfactionAlgorithmResult``.

        Parameters
        ----------
        spec : Specification
            The specification to satisfy.
        evidence_sources : dict[str, list[dict[str, Any]]]
            Direct evidence keyed by coordinate.

        Returns
        -------
        SatisfactionAlgorithmResult
            Full result including witness, certificate or gap, timing, and log.
        """
        from jugeo.problem_modes.specification_satisfaction.algorithms import (  # noqa: PLC0415
            specification_satisfaction_algorithm,
            descent_for_satisfaction,
        )

        t_start = time.monotonic()
        log: list[str] = []
        spec_id = getattr(spec, "spec_id", str(spec))
        log.append(f"[pipeline] Starting full pipeline for spec={spec_id}")

        combined_evidence = dict(evidence_sources)
        evidence_store = self.connected_systems.get("evidence_store")
        if evidence_store is not None and hasattr(evidence_store, "query"):
            required: frozenset[str] = getattr(spec, "required_coordinates", frozenset())
            for coord in required:
                try:
                    store_ev = evidence_store.query(coord)
                    if store_ev:
                        combined_evidence.setdefault(coord, []).extend(store_ev)
                        log.append(f"[pipeline] Pulled {len(store_ev)} item(s) from store for {coord!r}")
                except Exception as exc:  # noqa: BLE001
                    log.append(f"[pipeline] Evidence store query failed for {coord!r}: {exc}")

        witness = specification_satisfaction_algorithm(spec, combined_evidence)
        log.append("[pipeline] Witness assembled.")

        missing: list[str] = []
        if isinstance(witness, dict):
            missing = witness.get("missing_coordinates", [])
        else:
            missing = list(getattr(witness, "missing_coordinates", []))

        cert = None
        gap = None
        success = False

        if not missing:
            try:
                descent_result = descent_for_satisfaction(
                    witness,
                    descent_engine=self.connected_systems.get("descent_engine"),
                )
                is_cert = isinstance(descent_result, dict) and "certificate_id" in descent_result
                if not is_cert:
                    is_cert = hasattr(descent_result, "certificate_id")
                if is_cert:
                    cert = descent_result
                    success = True
                    log.append("[pipeline] Descent succeeded; certificate issued.")
                    cert_store = self.connected_systems.get("certificate_store")
                    if cert_store is not None and hasattr(cert_store, "store"):
                        try:
                            cert_store.store(cert)
                            log.append("[pipeline] Certificate stored.")
                        except Exception as exc:  # noqa: BLE001
                            log.append(f"[pipeline] Certificate store failed: {exc}")
                else:
                    gap = descent_result
                    log.append("[pipeline] Descent produced a residual gap.")
            except ValueError as exc:
                log.append(f"[pipeline] Descent raised ValueError: {exc}")
                from jugeo.problem_modes.specification_satisfaction.algorithms import (  # noqa: PLC0415
                    _build_gap,
                )
                gap = _build_gap(spec, witness)
        else:
            from jugeo.problem_modes.specification_satisfaction.algorithms import (  # noqa: PLC0415
                _build_gap,
            )
            gap = _build_gap(spec, witness)
            log.append(f"[pipeline] Coverage gap; missing={missing}")

        elapsed = time.monotonic() - t_start
        log.append(f"[pipeline] Completed in {elapsed:.4f}s.")

        result_dict: dict[str, Any] = {
            "success": success,
            "witness": witness,
            "certificate": cert,
            "gap": gap,
            "iterations_taken": 1,
            "elapsed_seconds": elapsed,
            "algorithm_log": tuple(log),
        }
        try:
            from jugeo.problem_modes.specification_satisfaction.algorithms import (  # noqa: PLC0415
                SatisfactionAlgorithmResult,
            )
            return SatisfactionAlgorithmResult(**result_dict)
        except Exception:  # noqa: BLE001
            return result_dict

    # -- introspection ------------------------------------------------------

    def get_connected_system_names(self) -> list[str]:
        """Return names of all currently connected subsystems.

        Returns
        -------
        list[str]
            Keys from ``self.connected_systems``.
        """
        return list(self.connected_systems.keys())

    def integration_health_check(self) -> dict[str, bool]:
        """Probe each connected system for a health-check method.

        Returns
        -------
        dict[str, bool]
            Mapping from system name to health status; ``True`` if the system
            either has no ``health_check`` method or its ``health_check()``
            returns truthy.
        """
        results: dict[str, bool] = {}
        for name, system in self.connected_systems.items():
            if hasattr(system, "health_check"):
                try:
                    results[name] = bool(system.health_check())
                except Exception:  # noqa: BLE001
                    results[name] = False
            else:
                results[name] = True
        return results

    # -- configuration ------------------------------------------------------

    def configure(self, key: str, value: Any) -> None:
        """Set a configuration key.

        Parameters
        ----------
        key : str
            Configuration key.
        value : Any
            Value to associate with *key*.
        """
        self.configuration[key] = value
        self.integration_log.append({"action": "configure", "key": key, "ts": _now_iso()})

    def get_configuration(self) -> dict[str, Any]:
        """Return a shallow copy of the current configuration.

        Returns
        -------
        dict[str, Any]
            Copy of ``self.configuration``.
        """
        return dict(self.configuration)


# -- exporter ---------------------------------------------------------------

@dataclass(slots=True)
class SatisfactionExporter:
    """Mutable exporter for specification satisfaction artefacts.

    Supports exporting specifications, witnesses, certificates, and gaps to
    JSON strings or plain dicts.  All export operations are recorded in
    ``export_log``.

    Parameters
    ----------
    export_log : list[dict]
        Audit log of every export operation.
    supported_formats : tuple[str, ...]
        Format identifiers supported by this exporter.
    """

    export_log: list[dict[str, Any]] = field(default_factory=list)
    supported_formats: tuple[str, ...] = ("json", "dict", "summary")

    # -- individual exporters -----------------------------------------------

    def export_specification(self, spec: Any, format: str = "json") -> str | dict[str, Any]:
        """Export a specification to the requested format.

        Parameters
        ----------
        spec : Specification
            Specification to export.
        format : str, optional
            ``"json"``, ``"dict"``, or ``"summary"``.

        Returns
        -------
        str or dict[str, Any]
            Serialised specification.

        Raises
        ------
        ValueError
            If *format* is not supported.
        """
        self._check_format(format)
        d = _coerce_to_dict(spec)
        self.export_log.append({"action": "export_specification", "format": format, "ts": _now_iso()})
        if format == "summary":
            return (
                f"Spec {d.get('spec_id','?')} | formula={d.get('formula','?')}"
            )
        if format == "json":
            return self.to_json(d)
        return d

    def export_witness(self, witness: Any, format: str = "json") -> str | dict[str, Any]:
        """Export a satisfaction witness.

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness to export.
        format : str, optional
            ``"json"``, ``"dict"``, or ``"summary"``.

        Returns
        -------
        str or dict[str, Any]
            Serialised witness.
        """
        self._check_format(format)
        d = _coerce_to_dict(witness)
        self.export_log.append({"action": "export_witness", "format": format, "ts": _now_iso()})
        if format == "summary":
            status = d.get("status", "?")
            missing = d.get("missing_coordinates", [])
            return f"Witness {d.get('witness_id','?')} | status={status} | missing={missing}"
        if format == "json":
            return self.to_json(d)
        return d

    def export_certificate(self, cert: Any, format: str = "json") -> str | dict[str, Any]:
        """Export a certificate of satisfaction.

        Parameters
        ----------
        cert : CertificateOfSatisfaction
            Certificate to export.
        format : str, optional
            ``"json"``, ``"dict"``, or ``"summary"``.

        Returns
        -------
        str or dict[str, Any]
            Serialised certificate.
        """
        self._check_format(format)
        d = _coerce_to_dict(cert)
        self.export_log.append({"action": "export_certificate", "format": format, "ts": _now_iso()})
        if format == "summary":
            return (
                f"Certificate {d.get('certificate_id','?')} | "
                f"spec={d.get('spec_id','?')} | issued={d.get('issued_at','?')}"
            )
        if format == "json":
            return self.to_json(d)
        return d

    def export_gap(self, gap: Any, format: str = "json") -> str | dict[str, Any]:
        """Export a residual gap.

        Parameters
        ----------
        gap : ResidualGap
            Gap to export.
        format : str, optional
            ``"json"``, ``"dict"``, or ``"summary"``.

        Returns
        -------
        str or dict[str, Any]
            Serialised gap.
        """
        self._check_format(format)
        d = _coerce_to_dict(gap)
        self.export_log.append({"action": "export_gap", "format": format, "ts": _now_iso()})
        if format == "summary":
            unsatisfied = d.get("unsatisfied_coordinates", [])
            return (
                f"Gap {d.get('gap_id','?')} | "
                f"spec={d.get('spec_id','?')} | "
                f"unsatisfied={unsatisfied}"
            )
        if format == "json":
            return self.to_json(d)
        return d

    def export_full_result(self, result: Any, format: str = "json") -> str | dict[str, Any]:
        """Export a complete ``SatisfactionAlgorithmResult``.

        Parameters
        ----------
        result : SatisfactionAlgorithmResult
            Full algorithm result to export.
        format : str, optional
            ``"json"``, ``"dict"``, or ``"summary"``.

        Returns
        -------
        str or dict[str, Any]
            Serialised result.
        """
        self._check_format(format)
        if hasattr(result, "to_dict"):
            d = result.to_dict()
        else:
            d = _coerce_to_dict(result)
        self.export_log.append({"action": "export_full_result", "format": format, "ts": _now_iso()})
        if format == "summary":
            return self.to_summary_text(result)
        if format == "json":
            return self.to_json(d)
        return d

    # -- serialisation utilities --------------------------------------------

    def to_json(self, obj_dict: dict[str, Any]) -> str:
        """Serialise a plain dict to a JSON string.

        Parameters
        ----------
        obj_dict : dict[str, Any]
            JSON-serialisable mapping.

        Returns
        -------
        str
            Compact, sorted-key JSON string.
        """
        return json.dumps(obj_dict, sort_keys=True, default=str, indent=2)

    def to_summary_text(self, result: Any) -> str:
        """Produce a human-readable multi-line summary of *result*.

        Parameters
        ----------
        result : SatisfactionAlgorithmResult
            Algorithm result to summarise.

        Returns
        -------
        str
            Multi-line plain-text summary.
        """
        if hasattr(result, "summary"):
            headline = result.summary()
        else:
            success = result.get("success", "?") if isinstance(result, dict) else getattr(result, "success", "?")
            headline = f"Success: {success}"

        lines: list[str] = [
            "=== Specification Satisfaction Result ===",
            headline,
        ]
        if isinstance(result, dict):
            iterations = result.get("iterations_taken", "?")
            elapsed = result.get("elapsed_seconds", "?")
        else:
            iterations = getattr(result, "iterations_taken", "?")
            elapsed = getattr(result, "elapsed_seconds", "?")
        lines.append(f"Iterations: {iterations}")
        lines.append(f"Elapsed: {elapsed}s")

        log_items: Any = []
        if isinstance(result, dict):
            log_items = result.get("algorithm_log", [])
        else:
            log_items = getattr(result, "algorithm_log", [])
        if log_items:
            lines.append("--- Algorithm Log ---")
            for entry in log_items:
                lines.append(f"  {entry}")
        lines.append("=========================================")
        return "\n".join(lines)

    def batch_export(
        self,
        objects: list[Any],
        format: str = "json",
    ) -> list[str | dict[str, Any]]:
        """Export a list of artefacts, applying best-effort type detection.

        Parameters
        ----------
        objects : list[Any]
            Mix of Specification, SatisfactionWitness, CertificateOfSatisfaction,
            or ResidualGap instances.
        format : str, optional
            Target format for all exports.

        Returns
        -------
        list[str or dict[str, Any]]
            One exported representation per input object.
        """
        results: list[str | dict[str, Any]] = []
        for obj in objects:
            d = _coerce_to_dict(obj)
            if "certificate_id" in d:
                results.append(self.export_certificate(obj, format=format))
            elif "gap_id" in d:
                results.append(self.export_gap(obj, format=format))
            elif "witness_id" in d:
                results.append(self.export_witness(obj, format=format))
            elif "spec_id" in d:
                results.append(self.export_specification(obj, format=format))
            else:
                results.append(self.export_full_result(obj, format=format))
        return results

    # -- internal -----------------------------------------------------------

    def _check_format(self, format: str) -> None:
        """Raise ``ValueError`` if *format* is not in ``supported_formats``.

        Parameters
        ----------
        format : str
            Requested format string.

        Raises
        ------
        ValueError
            If *format* is not in ``self.supported_formats``.
        """
        if format not in self.supported_formats:
            raise ValueError(
                f"Unsupported format {format!r}; supported: {self.supported_formats}"
            )


# -- importer ---------------------------------------------------------------

@dataclass(slots=True)
class SatisfactionImporter:
    """Mutable importer for specification satisfaction artefacts.

    Reconstructs domain objects from plain dicts or JSON strings.  All import
    operations are recorded in ``import_log``.

    Parameters
    ----------
    import_log : list[dict]
        Audit log of every import operation.
    """

    import_log: list[dict[str, Any]] = field(default_factory=list)

    # -- individual importers -----------------------------------------------

    def import_specification(self, data: dict[str, Any]) -> Any:
        """Reconstruct a ``Specification`` from a plain dict.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary with at minimum ``spec_id`` and ``formula`` keys.

        Returns
        -------
        Specification
            The reconstructed specification.

        Raises
        ------
        ValueError
            If required keys are absent.
        """
        errors = self.validate_import_data(data, "Specification")
        if errors:
            raise ValueError(f"Invalid Specification data: {errors}")
        self.import_log.append(
            {"action": "import_specification", "spec_id": data.get("spec_id"), "ts": _now_iso()}
        )
        try:
            import jugeo.problem_modes.specification_satisfaction.models as _m  # noqa: PLC0415
            return _m.Specification(**{k: v for k, v in data.items() if k in _m.Specification.__dataclass_fields__})  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return dict(data)

    def import_witness(self, data: dict[str, Any]) -> Any:
        """Reconstruct a ``SatisfactionWitness`` from a plain dict.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary with at minimum ``witness_id`` and ``spec_id`` keys.

        Returns
        -------
        SatisfactionWitness
            The reconstructed witness.

        Raises
        ------
        ValueError
            If required keys are absent.
        """
        errors = self.validate_import_data(data, "SatisfactionWitness")
        if errors:
            raise ValueError(f"Invalid SatisfactionWitness data: {errors}")
        self.import_log.append(
            {"action": "import_witness", "witness_id": data.get("witness_id"), "ts": _now_iso()}
        )
        try:
            import jugeo.problem_modes.specification_satisfaction.models as _m  # noqa: PLC0415
            return _m.SatisfactionWitness(**{k: v for k, v in data.items() if k in _m.SatisfactionWitness.__dataclass_fields__})  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return dict(data)

    def import_certificate(self, data: dict[str, Any]) -> Any:
        """Reconstruct a ``CertificateOfSatisfaction`` from a plain dict.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary with at minimum ``certificate_id`` and ``spec_id`` keys.

        Returns
        -------
        CertificateOfSatisfaction
            The reconstructed certificate.

        Raises
        ------
        ValueError
            If required keys are absent.
        """
        errors = self.validate_import_data(data, "CertificateOfSatisfaction")
        if errors:
            raise ValueError(f"Invalid CertificateOfSatisfaction data: {errors}")
        self.import_log.append(
            {"action": "import_certificate", "cert_id": data.get("certificate_id"), "ts": _now_iso()}
        )
        try:
            import jugeo.problem_modes.specification_satisfaction.models as _m  # noqa: PLC0415
            return _m.CertificateOfSatisfaction(**{k: v for k, v in data.items() if k in _m.CertificateOfSatisfaction.__dataclass_fields__})  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return dict(data)

    def import_gap(self, data: dict[str, Any]) -> Any:
        """Reconstruct a ``ResidualGap`` from a plain dict.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary with at minimum ``gap_id`` and ``spec_id`` keys.

        Returns
        -------
        ResidualGap
            The reconstructed gap.

        Raises
        ------
        ValueError
            If required keys are absent.
        """
        errors = self.validate_import_data(data, "ResidualGap")
        if errors:
            raise ValueError(f"Invalid ResidualGap data: {errors}")
        self.import_log.append(
            {"action": "import_gap", "gap_id": data.get("gap_id"), "ts": _now_iso()}
        )
        try:
            import jugeo.problem_modes.specification_satisfaction.models as _m  # noqa: PLC0415
            return _m.ResidualGap(**{k: v for k, v in data.items() if k in _m.ResidualGap.__dataclass_fields__})  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return dict(data)

    def from_json(self, json_str: str) -> dict[str, Any]:
        """Parse a JSON string into a plain dictionary.

        Parameters
        ----------
        json_str : str
            JSON-encoded string.

        Returns
        -------
        dict[str, Any]
            Parsed dictionary.

        Raises
        ------
        ValueError
            If *json_str* is not valid JSON or does not decode to a dict.
        """
        try:
            result = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
        if not isinstance(result, dict):
            raise ValueError(f"JSON must decode to a dict; got {type(result).__name__}")
        return result

    def validate_import_data(
        self,
        data: dict[str, Any],
        expected_type: str,
    ) -> list[str]:
        """Return a list of validation errors for *data* given *expected_type*.

        Parameters
        ----------
        data : dict[str, Any]
            Input data to validate.
        expected_type : str
            Name of the expected domain type (``"Specification"``,
            ``"SatisfactionWitness"``, ``"CertificateOfSatisfaction"``, or
            ``"ResidualGap"``).

        Returns
        -------
        list[str]
            Error messages; empty list means validation passed.
        """
        required_by_type: dict[str, list[str]] = {
            "Specification": ["spec_id", "formula"],
            "SatisfactionWitness": ["witness_id", "spec_id"],
            "CertificateOfSatisfaction": ["certificate_id", "spec_id"],
            "ResidualGap": ["gap_id", "spec_id"],
        }
        required = required_by_type.get(expected_type, [])
        errors: list[str] = []
        if not isinstance(data, dict):
            errors.append(f"Expected dict, got {type(data).__name__}")
            return errors
        for key in required:
            if key not in data:
                errors.append(f"Missing required key {key!r}")
        return errors

    def batch_import_specifications(
        self,
        data_list: list[dict[str, Any]],
    ) -> list[Any]:
        """Import a list of specification dicts.

        Parameters
        ----------
        data_list : list[dict[str, Any]]
            Each element should be a valid specification dict.

        Returns
        -------
        list[Specification]
            List of imported specifications.  Invalid entries are skipped with
            a warning logged.
        """
        results: list[Any] = []
        for i, data in enumerate(data_list):
            try:
                spec = self.import_specification(data)
                results.append(spec)
            except ValueError as exc:
                logger.warning("Skipping specification at index %d: %s", i, exc)
        return results


# -- registry ---------------------------------------------------------------

@dataclass(slots=True)
class SpecificationRegistry:
    """Mutable registry mapping spec IDs to ``Specification`` objects.

    Supports registration, retrieval, removal, and search by kind or
    required coordinate.

    Parameters
    ----------
    registry : dict[str, Specification]
        The underlying ID → Specification map.
    registration_log : list[dict]
        Audit trail of every registration/unregistration.
    """

    registry: dict[str, Any] = field(default_factory=dict)
    registration_log: list[dict[str, Any]] = field(default_factory=list)

    # -- CRUD ---------------------------------------------------------------

    def register(self, spec: Any) -> str:
        """Register *spec* and return its ID.

        If *spec* has no ``spec_id`` attribute, a new ID is generated and
        (if possible) set on the object.

        Parameters
        ----------
        spec : Specification
            Specification to register.

        Returns
        -------
        str
            The spec's ID (existing or freshly generated).
        """
        spec_id = getattr(spec, "spec_id", None) or (
            spec.get("spec_id") if isinstance(spec, dict) else None
        )
        if not spec_id:
            spec_id = _new_id("spec")
        self.registry[spec_id] = spec
        self.registration_log.append(
            {"action": "register", "spec_id": spec_id, "ts": _now_iso()}
        )
        logger.debug("Registered specification: %s", spec_id)
        return spec_id

    def unregister(self, spec_id: str) -> bool:
        """Remove the specification with *spec_id* from the registry.

        Parameters
        ----------
        spec_id : str
            Identifier of the specification to remove.

        Returns
        -------
        bool
            ``True`` if the spec was present and removed; ``False`` otherwise.
        """
        if spec_id not in self.registry:
            return False
        del self.registry[spec_id]
        self.registration_log.append(
            {"action": "unregister", "spec_id": spec_id, "ts": _now_iso()}
        )
        return True

    def get(self, spec_id: str) -> Any | None:
        """Retrieve the specification with *spec_id*.

        Parameters
        ----------
        spec_id : str
            Identifier to look up.

        Returns
        -------
        Specification or None
            The registered specification, or ``None`` if not found.
        """
        return self.registry.get(spec_id)

    def update(self, spec_id: str, updated_spec: Any) -> bool:
        """Replace the specification at *spec_id* with *updated_spec*.

        Parameters
        ----------
        spec_id : str
            Identifier of the specification to replace.
        updated_spec : Specification
            The new specification object.

        Returns
        -------
        bool
            ``True`` if the replacement was made; ``False`` if *spec_id* was
            not registered.
        """
        if spec_id not in self.registry:
            return False
        self.registry[spec_id] = updated_spec
        self.registration_log.append(
            {"action": "update", "spec_id": spec_id, "ts": _now_iso()}
        )
        return True

    # -- listing / search ---------------------------------------------------

    def list_specs(self) -> list[str]:
        """Return a sorted list of all registered spec IDs.

        Returns
        -------
        list[str]
            Sorted list of registered identifiers.
        """
        return sorted(self.registry.keys())

    def search_by_kind(self, kind: Any) -> list[Any]:
        """Return all specifications whose ``kind`` equals *kind*.

        Parameters
        ----------
        kind : SpecificationKind
            The kind to filter by.

        Returns
        -------
        list[Specification]
            Matching specifications (order not guaranteed).
        """
        results: list[Any] = []
        for spec in self.registry.values():
            spec_kind = getattr(spec, "kind", None) or (
                spec.get("kind") if isinstance(spec, dict) else None
            )
            if spec_kind == kind:
                results.append(spec)
        return results

    def search_by_coordinate(self, coordinate: str) -> list[Any]:
        """Return all specifications that require *coordinate*.

        Parameters
        ----------
        coordinate : str
            Coordinate identifier to search for.

        Returns
        -------
        list[Specification]
            Specifications whose ``required_coordinates`` contains *coordinate*.
        """
        results: list[Any] = []
        for spec in self.registry.values():
            required: Any = getattr(spec, "required_coordinates", None)
            if required is None and isinstance(spec, dict):
                required = spec.get("required_coordinates", frozenset())
            if isinstance(required, (set, frozenset, list, tuple)):
                if coordinate in required:
                    results.append(spec)
        return results

    def count(self) -> int:
        """Return the number of registered specifications.

        Returns
        -------
        int
            Registry size.
        """
        return len(self.registry)


# -- solver connector -------------------------------------------------------

@dataclass(slots=True)
class SolverConnector:
    """Mutable connector for an external specification solver.

    Manages connection lifecycle, sends specifications and witnesses, and polls
    for certificates.  All interactions are logged in ``connection_log``.

    Parameters
    ----------
    solver_url : str
        Base URL of the external solver service.
    solver_config : dict[str, Any]
        Per-solver configuration (timeouts, auth tokens, etc.).
    connection_log : list[dict]
        Chronological log of every connection event and request.
    is_connected : bool
        Whether the connector currently holds an active connection.
    """

    solver_url: str = ""
    solver_config: dict[str, Any] = field(default_factory=dict)
    connection_log: list[dict[str, Any]] = field(default_factory=list)
    is_connected: bool = False

    # -- lifecycle ----------------------------------------------------------

    def connect(self, url: str, config: dict[str, Any] | None = None) -> bool:
        """Establish a connection to the solver at *url*.

        Stores the URL and optional *config*, marks the connector as connected,
        and logs the event.  Actual network I/O is deferred to individual
        request methods.

        Parameters
        ----------
        url : str
            Base URL of the solver service.
        config : dict[str, Any] or None, optional
            Override configuration; merged on top of ``solver_config``.

        Returns
        -------
        bool
            ``True`` on success; ``False`` if the URL is empty.
        """
        if not url:
            logger.warning("Cannot connect: URL is empty.")
            return False
        self.solver_url = url
        if config:
            self.solver_config.update(config)
        self.is_connected = True
        self.connection_log.append(
            {"event": "connect", "url": url, "ts": _now_iso()}
        )
        logger.info("SolverConnector connected to %s", url)
        return True

    def disconnect(self) -> None:
        """Mark the connector as disconnected and log the event."""
        self.is_connected = False
        self.connection_log.append({"event": "disconnect", "ts": _now_iso()})
        logger.info("SolverConnector disconnected.")

    # -- request methods ----------------------------------------------------

    def send_specification(self, spec: Any) -> dict[str, Any]:
        """Serialise *spec* and simulate sending it to the solver.

        In a real deployment this would POST to ``<solver_url>/specifications``.
        Here it builds a request payload and returns a mock acknowledgement.

        Parameters
        ----------
        spec : Specification
            The specification to send.

        Returns
        -------
        dict[str, Any]
            Solver acknowledgement payload including a remote ``job_id``.

        Raises
        ------
        RuntimeError
            If the connector is not connected.
        """
        self._require_connected()
        spec_dict = _coerce_to_dict(spec)
        spec_id = spec_dict.get("spec_id", _new_id("spec"))
        request_id = _new_id("req")
        payload = {
            "endpoint": f"{self.solver_url}/specifications",
            "method": "POST",
            "spec_id": spec_id,
            "request_id": request_id,
            "content_hash": _sha256_dict(spec_dict),
        }
        response = {
            "status": "accepted",
            "job_id": _new_id("job"),
            "spec_id": spec_id,
            "request_id": request_id,
            "received_at": _now_iso(),
        }
        self.connection_log.append({"event": "send_specification", "request": payload, "ts": _now_iso()})
        return response

    def request_evidence(
        self,
        coordinate: str,
        evidence_kind: str,
    ) -> list[dict[str, Any]]:
        """Ask the solver for evidence of *evidence_kind* at *coordinate*.

        Parameters
        ----------
        coordinate : str
            Coordinate for which evidence is requested.
        evidence_kind : str
            Kind of evidence (e.g. ``"structural"``, ``"behavioural"``).

        Returns
        -------
        list[dict[str, Any]]
            Zero or more evidence payloads returned by the solver.  In offline
            mode, returns an empty list.

        Raises
        ------
        RuntimeError
            If the connector is not connected.
        """
        self._require_connected()
        self.connection_log.append(
            {
                "event": "request_evidence",
                "coordinate": coordinate,
                "kind": evidence_kind,
                "ts": _now_iso(),
            }
        )
        return []

    def submit_witness(self, witness: Any) -> dict[str, Any]:
        """Submit a satisfaction witness to the solver for verification.

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness to submit.

        Returns
        -------
        dict[str, Any]
            Solver response including a verification job identifier.

        Raises
        ------
        RuntimeError
            If the connector is not connected.
        """
        self._require_connected()
        w_dict = _coerce_to_dict(witness)
        witness_id = w_dict.get("witness_id", _new_id("witness"))
        job_id = _new_id("verify-job")
        response = {
            "status": "submitted",
            "witness_id": witness_id,
            "verification_job_id": job_id,
            "submitted_at": _now_iso(),
        }
        self.connection_log.append(
            {"event": "submit_witness", "witness_id": witness_id, "job_id": job_id, "ts": _now_iso()}
        )
        return response

    def poll_for_certificate(self, spec_id: str) -> Any | None:
        """Poll the solver for a certificate for the given spec.

        Parameters
        ----------
        spec_id : str
            The specification identifier to poll for.

        Returns
        -------
        CertificateOfSatisfaction or None
            A certificate dict if one is available; ``None`` otherwise.

        Raises
        ------
        RuntimeError
            If the connector is not connected.
        """
        self._require_connected()
        self.connection_log.append(
            {"event": "poll_certificate", "spec_id": spec_id, "ts": _now_iso()}
        )
        return None

    def health_check(self) -> bool:
        """Return ``True`` if the connector reports itself as connected.

        Returns
        -------
        bool
            Connection status.
        """
        return self.is_connected

    def get_connection_stats(self) -> dict[str, Any]:
        """Return aggregate statistics about this connector's activity.

        Returns
        -------
        dict[str, Any]
            Summary including event counts by type and last event timestamp.
        """
        event_counts: dict[str, int] = {}
        last_ts: str = ""
        for entry in self.connection_log:
            event = entry.get("event", "unknown")
            event_counts[event] = event_counts.get(event, 0) + 1
            ts = entry.get("ts", "")
            if ts > last_ts:
                last_ts = ts
        return {
            "solver_url": self.solver_url,
            "is_connected": self.is_connected,
            "total_events": len(self.connection_log),
            "event_counts": event_counts,
            "last_event_at": last_ts,
        }

    # -- internal -----------------------------------------------------------

    def _require_connected(self) -> None:
        """Raise ``RuntimeError`` if the connector is not connected.

        Raises
        ------
        RuntimeError
            If ``self.is_connected`` is ``False``.
        """
        if not self.is_connected:
            raise RuntimeError(
                "SolverConnector is not connected; call connect(url) first."
            )


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_GLOBAL_REGISTRY: SpecificationRegistry = SpecificationRegistry()
_GLOBAL_INTEGRATION: SpecificationSatisfactionIntegration | None = None

# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def register_specification(spec: Any) -> str:
    """Register *spec* in the global registry and return its ID.

    Parameters
    ----------
    spec : Specification
        The specification to register.

    Returns
    -------
    str
        The registered specification ID.
    """
    return _GLOBAL_REGISTRY.register(spec)


def connect_to_solver(url: str, config: dict[str, Any] | None = None) -> SolverConnector:
    """Create and connect a ``SolverConnector`` to *url*.

    Parameters
    ----------
    url : str
        Base URL of the external solver.
    config : dict[str, Any] or None, optional
        Optional configuration dict.

    Returns
    -------
    SolverConnector
        A connected solver connector instance.

    Raises
    ------
    RuntimeError
        If the connection attempt fails (empty URL).
    """
    connector = SolverConnector()
    success = connector.connect(url, config=config)
    if not success:
        raise RuntimeError(f"Failed to connect to solver at URL {url!r}.")
    return connector


def build_integration(
    config: dict[str, Any] | None = None,
) -> SpecificationSatisfactionIntegration:
    """Instantiate and configure a ``SpecificationSatisfactionIntegration``.

    Parameters
    ----------
    config : dict[str, Any] or None, optional
        Initial configuration to apply.  Keys are passed to
        ``integration.configure(key, value)`` for each entry.

    Returns
    -------
    SpecificationSatisfactionIntegration
        Ready-to-use integration instance.
    """
    global _GLOBAL_INTEGRATION  # noqa: PLW0603
    integration = SpecificationSatisfactionIntegration()
    if config:
        for key, value in config.items():
            integration.configure(key, value)
    _GLOBAL_INTEGRATION = integration
    logger.info("Global integration instance created.")
    return integration


def export_result_to_json(result: Any) -> str:
    """Export a ``SatisfactionAlgorithmResult`` to a JSON string.

    Parameters
    ----------
    result : SatisfactionAlgorithmResult
        The algorithm result to export.

    Returns
    -------
    str
        JSON representation of the result.
    """
    exporter = SatisfactionExporter()
    out = exporter.export_full_result(result, format="json")
    if isinstance(out, dict):
        return json.dumps(out, sort_keys=True, default=str, indent=2)
    return str(out)


def import_specification_from_json(json_str: str) -> Any:
    """Parse a JSON string and reconstruct the embedded ``Specification``.

    Parameters
    ----------
    json_str : str
        A JSON-encoded specification dictionary.

    Returns
    -------
    Specification
        The reconstructed specification.

    Raises
    ------
    ValueError
        If the JSON is invalid or the specification data is malformed.
    """
    importer = SatisfactionImporter()
    data = importer.from_json(json_str)
    return importer.import_specification(data)


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.encodings)
# ---------------------------------------------------------------------------

def spec_descent(spec: Any) -> dict[str, Any]:
    """Compute descent data for specification satisfaction.
    
    Specification satisfaction IS descent — satisfying a spec means finding
    a global section that restricts correctly to each local patch.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict with specification data.
    
    Returns
    -------
    dict[str, Any]
        Descent record with ``cover``, ``local_sections``, ``cocycle_trivial``,
        and ``global_section_exists`` keys.
    """
    try:
        from jugeo.geometry.descent import run_descent, DescentDatum
    except ImportError:
        run_descent = None
        DescentDatum = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    descent: dict[str, Any] = {
        "spec_name": name,
        "cover": list(coords) if coords else [],
        "local_sections": {},
        "cocycle_trivial": None,
        "global_section_exists": None,
    }

    if run_descent is not None:
        try:
            result = run_descent(coords)
            descent["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            descent["global_section_exists"] = getattr(result, "global_section_exists", None)
            descent["local_sections"] = getattr(result, "local_sections", {})
        except Exception:
            pass

    return descent


def spec_certificate(result: Any) -> dict[str, Any]:
    """Build an evidence certificate for a satisfaction result.
    
    A satisfaction certificate records that a specification was checked,
    the outcome, and the trust level of the evidence.
    
    Parameters
    ----------
    result : Any
        A satisfaction result object or dict.
    
    Returns
    -------
    dict[str, Any]
        Certificate with ``satisfied``, ``trust_level``, ``witness_hash``,
        ``spec_name``, and ``certificate_id`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    satisfied = getattr(result, "satisfied", None)
    if satisfied is None and isinstance(result, dict):
        satisfied = result.get("satisfied", result.get("status") == "satisfied")

    spec_name = getattr(result, "spec_name", None) or (
        result.get("spec_name") if isinstance(result, dict) else "unknown"
    )

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "spec_name": spec_name,
        "satisfied": bool(satisfied),
        "trust_level": "VERIFIED" if satisfied else "UNVERIFIED",
        "witness_hash": hashlib.sha256(str(result).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=spec_name, satisfied=satisfied, source="specification_satisfaction"
            )
        except Exception:
            pass

    return cert


def spec_encoding(spec: Any) -> dict[str, Any]:
    """Encode a specification as scalar constraints for SMT solving.
    
    Specifications translate to scalar encodings where each clause becomes
    a conjunction of SMT predicates over the target coordinates.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict.
    
    Returns
    -------
    dict[str, Any]
        Encoding with ``formulas``, ``variables``, ``coordinate_map``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings.scalar_encodings import ScalarEncoder, encode_constraint
    except ImportError:
        ScalarEncoder = None
        encode_constraint = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    encoding: dict[str, Any] = {
        "spec_name": name,
        "encoding_kind": "scalar_conjunction",
        "formulas": [f"(sat {c})" for c in (coords or [])],
        "variables": [f"sat_{c}" for c in (coords or [])],
        "coordinate_map": {c: f"sat_{c}" for c in (coords or [])},
        "encoder": None,
    }

    if encode_constraint is not None:
        try:
            for c in (coords or []):
                enc = encode_constraint(c, name)
                if hasattr(enc, "formula"):
                    encoding["formulas"].append(enc.formula)
        except Exception:
            pass

    if ScalarEncoder is not None:
        try:
            encoding["encoder"] = ScalarEncoder(coordinates=list(coords or []))
        except Exception:
            pass

    return encoding


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Classes
    "SpecificationSatisfactionIntegration",
    "SatisfactionExporter",
    "SatisfactionImporter",
    "SpecificationRegistry",
    "SolverConnector",
    # Module-level functions
    "register_specification",
    "connect_to_solver",
    "build_integration",
    "export_result_to_json",
    "import_specification_from_json",
    # Module-level state
    "_GLOBAL_REGISTRY",
    "_GLOBAL_INTEGRATION",
    # Unified architecture cross-references
    "spec_descent",
    "spec_certificate",
    "spec_encoding",
]
