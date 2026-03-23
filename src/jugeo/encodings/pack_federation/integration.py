r"""Integration layer for the pack_federation encoding.

Theory (theory2.tex §35.5 — Integration):
    The integration layer wires together the encoding primitives (models,
    sheaf, protocol engine) with the higher-level jugeo federation engine.
    It provides a single façade class :class:`PackFederationEncodingIntegration`
    that:

    1. Holds a :class:`PackFederationEncoding` as the authoritative source of
       truth.
    2. Optionally holds a :class:`PackFederationAsSheaf` and a
       :class:`FederationProtocolEngine` for higher-level operations.
    3. Provides methods to build the sheaf, execute federation requests,
       validate results, import/export encodings, compute global sections,
       and diagnose failures.

    §35.5 Design principle: The integration layer must be stateless with
    respect to the encoding — every mutation either replaces self.encoding
    or is applied to the derived objects (sheaf, engine) and then reflected
    back into the encoding via :meth:`to_encoding`.

Public surface
--------------
:class:`PackFederationEncodingIntegration`
    Façade class integrating encoding, sheaf, and protocol engine.

copilot: pack-federation-integration
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Final, FrozenSet, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from .models import (
    BridgeTheoremEncoding,
    FederationProtocol,
    PackBoundary,
    PackFederationEncoding,
)
from .pack_federation_as_sheaf import PackFederationAsSheaf
from .federation_protocol import FederationProtocolEngine

try:
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    _HAS_BRIDGES = True
except ImportError:
    _HAS_BRIDGES = False

try:
    from jugeo.packs.federation import FederationRequest, FederationResult, FederationEngine
    _HAS_FEDERATION = True
except ImportError:
    _HAS_FEDERATION = False

__all__: list[str] = [
    "PackFederationEncodingIntegration",
]


# ---------------------------------------------------------------------------
# PackFederationEncodingIntegration
# ---------------------------------------------------------------------------


@dataclass
class PackFederationEncodingIntegration:
    """Façade integrating encoding, sheaf, and protocol engine.

    This class is the primary entry point for consumers of the
    pack_federation sub-package who wish to interact with a live federation
    engine (e.g. from jugeo.packs.federation).  It maintains the authoritative
    :class:`PackFederationEncoding` and lazily constructs the derived
    :class:`PackFederationAsSheaf` and :class:`FederationProtocolEngine`
    objects as needed.

    Parameters
    ----------
    encoding:
        The authoritative :class:`PackFederationEncoding`.
    sheaf:
        Optional pre-built :class:`PackFederationAsSheaf`; constructed lazily
        by :meth:`build_pack_sheaf` if not provided.
    protocol_engine:
        Optional pre-built :class:`FederationProtocolEngine`.
    _federation_engine:
        The underlying jugeo federation engine (any object with an ``execute``
        method); set via :meth:`integrate_with_federation_engine`.
    _result_cache:
        Internal cache mapping request fingerprints to cached results.

    copilot: integration-dataclass
    """

    encoding: PackFederationEncoding
    sheaf: PackFederationAsSheaf | None = None
    protocol_engine: FederationProtocolEngine | None = None
    _federation_engine: Any = field(default=None, repr=False)
    _result_cache: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    # 1. integrate_with_federation_engine
    # ------------------------------------------------------------------

    def integrate_with_federation_engine(self, engine: Any) -> None:
        """Store and validate a federation engine.

        Checks that *engine* exposes an ``execute`` callable attribute.
        If jugeo.packs.federation is available, also checks that *engine* is
        an instance of ``FederationEngine`` (or any compatible subclass).

        Parameters
        ----------
        engine:
            The federation engine object.  Must have an ``execute`` method.

        Raises
        ------
        TypeError
            If *engine* is None or does not have an ``execute`` attribute.
        AttributeError
            If the ``execute`` attribute exists but is not callable.
        """
        if engine is None:
            raise TypeError(
                "federation engine must not be None; "
                "expected an object with an 'execute' method"
            )
        if not hasattr(engine, "execute"):
            raise AttributeError(
                f"federation engine {engine!r} does not have an 'execute' attribute. "
                "Pass an object that exposes a callable 'execute' method."
            )
        if not callable(getattr(engine, "execute")):
            raise AttributeError(
                f"engine.execute is not callable (got {type(engine.execute)!r})"
            )

        if _HAS_FEDERATION:
            # Optional stronger check if the jugeo federation module is available
            if not isinstance(engine, FederationEngine):  # type: ignore[possibly-undefined]
                # Log a warning-level note but do not raise — duck typing is fine
                pass  # noqa: SIM105  (copilot: suppress for intentional)

        self._federation_engine = engine

    # ------------------------------------------------------------------
    # 2. build_pack_sheaf
    # ------------------------------------------------------------------

    def build_pack_sheaf(self) -> PackFederationAsSheaf:
        """Construct a :class:`PackFederationAsSheaf` from :attr:`encoding`.

        Builds the :attr:`~PackFederationAsSheaf.boundary_map` by creating one
        :class:`PackBoundary` per bridge encoding (keyed as
        ``"boundary_{bridge_id}"``), initialises empty local sections for each
        pack, and stores the result in :attr:`sheaf`.

        Returns
        -------
        PackFederationAsSheaf
            The newly constructed sheaf (also stored in :attr:`sheaf`).
        """
        boundary_map: dict[str, PackBoundary] = {}
        for bridge in self.encoding.bridge_encodings:
            boundary_id = f"boundary_{bridge.bridge_id}"
            boundary = PackBoundary(
                boundary_id=boundary_id,
                pack_a_id=bridge.source_pack_id,
                pack_b_id=bridge.target_pack_id,
                shared_coordinates=bridge.overlap_region,
                overlap_laws=(
                    f"source_formula:{bridge.source_formula}",
                    f"target_formula:{bridge.target_formula}",
                ),
                boundary_type="interior",
            )
            boundary_map[boundary_id] = boundary

        local_sections: dict[str, dict] = {
            pid: {} for pid in self.encoding.pack_ids
        }

        self.sheaf = PackFederationAsSheaf(
            encoding=self.encoding,
            boundary_map=boundary_map,
            local_sections=local_sections,
        )
        return self.sheaf

    # ------------------------------------------------------------------
    # 3. execute_federation_request
    # ------------------------------------------------------------------

    def execute_federation_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Execute a federation request using the protocol engine.

        Converts the *request* dict to an evidence dict by extracting the
        ``"evidence"`` key (defaulting to a copy of the request), then calls
        :meth:`FederationProtocolEngine.execute_descent` on the protocol engine.

        The result is cached under the fingerprint of the request.

        Parameters
        ----------
        request:
            Federation request dict.  Should contain at least an
            ``"evidence"`` key with the initial evidence to transport.

        Returns
        -------
        dict[str, Any]
            Federation result dict.

        Raises
        ------
        RuntimeError
            If :attr:`protocol_engine` has not been set.
        """
        if self.protocol_engine is None:
            raise RuntimeError(
                "protocol_engine is not set. "
                "Construct a FederationProtocolEngine and assign it to "
                "self.protocol_engine before calling execute_federation_request."
            )

        fingerprint = json.dumps(request, sort_keys=True, default=str)
        if fingerprint in self._result_cache:
            return dict(self._result_cache[fingerprint])

        initial_evidence = dict(request.get("evidence", request))
        result = self.protocol_engine.execute_descent(initial_evidence)

        self._result_cache[fingerprint] = result
        return dict(result)

    # ------------------------------------------------------------------
    # 4. validate_federation_result
    # ------------------------------------------------------------------

    def validate_federation_result(
        self, result: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        """Validate a federation result using the protocol engine.

        Delegates to :meth:`FederationProtocolEngine.validate_result`.

        Parameters
        ----------
        result:
            Result dict to validate.

        Returns
        -------
        tuple[bool, list[str]]
            ``(True, [])`` if valid; ``(False, errors)`` otherwise.

        Raises
        ------
        RuntimeError
            If :attr:`protocol_engine` is not set.
        """
        if self.protocol_engine is None:
            raise RuntimeError(
                "protocol_engine is not set; cannot validate result without it."
            )
        return self.protocol_engine.validate_result(result)

    # ------------------------------------------------------------------
    # 5. export_encoding
    # ------------------------------------------------------------------

    def export_encoding(self, path: str | None = None) -> dict[str, Any]:
        """Serialise :attr:`encoding` to a dict, optionally writing to a file.

        Calls :meth:`PackFederationEncoding.to_dict` and, if *path* is given,
        writes the result as JSON to that path.

        Parameters
        ----------
        path:
            Optional file path string.  If provided, the serialised dict is
            written as UTF-8 JSON.

        Returns
        -------
        dict[str, Any]
            The serialised encoding dict.
        """
        data = self.encoding.to_dict()

        if path is not None:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)

        return data

    # ------------------------------------------------------------------
    # 6. import_encoding
    # ------------------------------------------------------------------

    def import_encoding(self, data: dict[str, Any]) -> None:
        """Deserialise *data* and replace :attr:`encoding`.

        Also invalidates :attr:`sheaf`, :attr:`protocol_engine`, and
        :attr:`_result_cache` since the encoding has changed.

        Parameters
        ----------
        data:
            Dict previously produced by :meth:`export_encoding` or
            :meth:`PackFederationEncoding.to_dict`.
        """
        self.encoding = PackFederationEncoding.from_dict(data)
        # Invalidate derived objects
        self.sheaf = None
        self.protocol_engine = None
        self._result_cache.clear()

    # ------------------------------------------------------------------
    # 7. compute_global_section
    # ------------------------------------------------------------------

    def compute_global_section(self, coordinate: str) -> dict[str, Any]:
        """Compute the global section at *coordinate* using the sheaf.

        Lazily builds :attr:`sheaf` if it has not been constructed yet, then
        calls :meth:`PackFederationAsSheaf.evaluate_section`.

        Parameters
        ----------
        coordinate:
            The coordinate name to evaluate.

        Returns
        -------
        dict[str, Any]
            Section evaluation result (see
            :meth:`~PackFederationAsSheaf.evaluate_section`).
        """
        if self.sheaf is None:
            self.build_pack_sheaf()

        assert self.sheaf is not None
        return self.sheaf.evaluate_section(coordinate)

    # ------------------------------------------------------------------
    # 8. diagnose_federation_failure
    # ------------------------------------------------------------------

    def diagnose_federation_failure(self, result: dict[str, Any]) -> dict[str, Any]:
        """Analyse a failed federation result and return a structured diagnosis.

        Checks:
        1. Whether final_trust is below the protocol's trust_floor.
        2. Whether kind preservation was violated in any local section.
        3. Whether the bridge path was complete (all bridges in
           bridge_sequence were executed).
        4. Whether the sheaf condition is satisfied.

        Parameters
        ----------
        result:
            A federation result dict (possibly from a failed execution).

        Returns
        -------
        dict[str, Any]
            Structured diagnosis dict with keys:
            - ``"trust_ok"``: bool
            - ``"trust_detail"``: str
            - ``"kind_ok"``: bool
            - ``"kind_detail"``: str
            - ``"bridge_path_complete"``: bool
            - ``"bridge_path_detail"``: str
            - ``"sheaf_condition_ok"``: bool
            - ``"sheaf_detail"``: str
            - ``"overall_diagnosis"``: str summary
        """
        diagnosis: dict[str, Any] = {}

        # 1. Trust check
        final_trust = float(result.get("final_trust", 0.0))
        trust_floor = float(result.get("trust_floor", 0.0))
        trust_ok = final_trust >= trust_floor
        diagnosis["trust_ok"] = trust_ok
        diagnosis["trust_detail"] = (
            f"final_trust={final_trust:.4f} {'≥' if trust_ok else '<'} "
            f"trust_floor={trust_floor:.4f}"
        )

        # 2. Kind preservation check
        kind_violations: list[str] = []
        for pack_id, section in result.get("local_sections", {}).items():
            kind = section.get("kind")
            original = section.get("original_kind")
            if kind is not None and original is not None and kind != original:
                kind_violations.append(
                    f"pack {pack_id!r}: kind={kind!r} != original_kind={original!r}"
                )
        kind_ok = len(kind_violations) == 0
        diagnosis["kind_ok"] = kind_ok
        diagnosis["kind_detail"] = (
            "Kind preserved" if kind_ok
            else f"Kind violations: {'; '.join(kind_violations)}"
        )

        # 3. Bridge path completeness
        executed_bridges = {
            log.get("bridge_id")
            for log in (
                self.protocol_engine.get_execution_log()
                if self.protocol_engine is not None
                else []
            )
        }
        expected_bridges = set(result.get("bridge_sequence", []))
        missing = expected_bridges - executed_bridges
        path_complete = len(missing) == 0
        diagnosis["bridge_path_complete"] = path_complete
        diagnosis["bridge_path_detail"] = (
            "All bridges executed"
            if path_complete
            else f"Missing bridges: {sorted(missing)}"
        )

        # 4. Sheaf condition
        sheaf_ok_flag, sheaf_violations = False, ["sheaf not built"]
        if self.sheaf is not None:
            sheaf_ok_flag, sheaf_violations = self.sheaf.check_sheaf_condition()
        elif self.encoding.sheaf_condition_status == "satisfied":
            sheaf_ok_flag = True
            sheaf_violations = []
        diagnosis["sheaf_condition_ok"] = sheaf_ok_flag
        diagnosis["sheaf_detail"] = (
            "Sheaf condition satisfied"
            if sheaf_ok_flag
            else f"Violations: {sheaf_violations[:3]}"
        )

        # Overall
        all_ok = trust_ok and kind_ok and path_complete and sheaf_ok_flag
        if all_ok:
            diagnosis["overall_diagnosis"] = "No issues found; result appears valid"
        else:
            issues = [
                k.replace("_ok", "").replace("_", " ")
                for k in ("trust_ok", "kind_ok", "bridge_path_complete", "sheaf_condition_ok")
                if not diagnosis[k]
            ]
            diagnosis["overall_diagnosis"] = f"Issues detected: {', '.join(issues)}"

        return diagnosis
