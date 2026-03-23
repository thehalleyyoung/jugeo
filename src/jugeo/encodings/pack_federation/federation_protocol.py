r"""Federation protocol engine — descent across pack boundaries.

Theory (theory2.tex §35.3 — Federation Protocol as Descent):
    The federation protocol operationalises the abstract sheaf structure of
    §35.1 into a concrete computation.  Given an ordered sequence of bridge
    theorems (bridge_sequence) and an initial evidence dict, the descent
    procedure threads the evidence through each bridge in order, at each step:

    1. Restricting the current evidence to the bridge's overlap_region.
    2. Applying the bridge's translation formula to produce evidence on the
       target pack.
    3. Enforcing the trust ceiling: the running trust is min(running,
       bridge.trust_ceiling) in strict mode (Lemma 35.7).
    4. Checking kind preservation: if kind_preservation_mode == "strict",
       the "kind" key in the evidence must not change.

    The descent terminates when either all steps succeed (producing a final
    global section) or a step fails (raising an error or returning a failure
    flag, depending on the protocol's kind_preservation_mode).

    §35 Theorem 35.2 (Completeness of descent):
        If all bridges in bridge_sequence satisfy the sheaf condition and have
        trust_ceiling >= protocol.trust_floor, then the descent always produces
        a valid global section with trust >= protocol.trust_floor.

Public surface
--------------
:class:`FederationProtocolEngine`
    Dataclass that executes a :class:`FederationProtocol` step by step.

copilot: federation-protocol-engine
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, Final, FrozenSet, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from .models import BridgeTheoremEncoding, FederationProtocol

__all__: list[str] = [
    "FederationProtocolEngine",
]


# ---------------------------------------------------------------------------
# FederationProtocolEngine
# ---------------------------------------------------------------------------


@dataclass
class FederationProtocolEngine:
    """Engine that executes a :class:`FederationProtocol` via descent.

    Wraps a :class:`FederationProtocol` together with a dict-based index of
    :class:`BridgeTheoremEncoding` objects so that each step in the protocol's
    ``bridge_sequence`` can be looked up and applied.

    Parameters
    ----------
    protocol:
        The protocol to execute.
    bridge_index:
        Dict mapping ``bridge_id`` to the corresponding
        :class:`BridgeTheoremEncoding`.
    execution_log:
        List accumulating one dict per step executed.
    _state:
        Internal mutable state dict; should not be set by caller.

    copilot: protocol-engine-dataclass
    """

    protocol: FederationProtocol
    bridge_index: dict[str, BridgeTheoremEncoding]
    execution_log: list[dict] = field(default_factory=list)
    _state: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    # 1. plan_execution
    # ------------------------------------------------------------------

    def plan_execution(self) -> list[dict[str, Any]]:
        """Build an ordered execution plan from the protocol's bridge sequence.

        Each step dict has the following keys:
        - ``step_idx``: zero-based index.
        - ``bridge_id``: the bridge identifier for this step.
        - ``source``: source_pack_id of the bridge (or ``"unknown"`` if bridge
          not found in :attr:`bridge_index`).
        - ``target``: target_pack_id of the bridge.
        - ``trust_ceiling``: trust ceiling from the bridge encoding.
        - ``preconditions``: list of descent_conditions strings from
          :attr:`protocol.descent_conditions` that mention this bridge_id (or
          all conditions if no matching filter is found).

        Returns
        -------
        list[dict[str, Any]]
            One plan-step dict per bridge in the protocol's bridge_sequence.
        """
        plan: list[dict[str, Any]] = []
        descent_conditions = list(self.protocol.descent_conditions)

        for step_idx, bridge_id in enumerate(self.protocol.bridge_sequence):
            bridge = self.bridge_index.get(bridge_id)
            if bridge is not None:
                source = bridge.source_pack_id
                target = bridge.target_pack_id
                trust_ceiling = bridge.trust_ceiling
            else:
                source = "unknown"
                target = "unknown"
                trust_ceiling = 1.0

            # Find descent conditions that mention this bridge_id
            relevant_conditions = [
                cond for cond in descent_conditions
                if bridge_id in cond
            ]
            if not relevant_conditions:
                relevant_conditions = list(descent_conditions)

            plan.append({
                "step_idx": step_idx,
                "bridge_id": bridge_id,
                "source": source,
                "target": target,
                "trust_ceiling": trust_ceiling,
                "preconditions": relevant_conditions,
            })

        return plan

    # ------------------------------------------------------------------
    # 2. execute_descent
    # ------------------------------------------------------------------

    def execute_descent(self, initial_evidence: dict[str, Any]) -> dict[str, Any]:
        """Execute the full descent, threading evidence through all bridge steps.

        Iterates over the execution plan produced by :meth:`plan_execution`,
        calling :meth:`execute_single_step` for each step.  Logs every step in
        :attr:`execution_log`.  The running trust is updated via
        :meth:`propagate_trust` at each step.

        Parameters
        ----------
        initial_evidence:
            Initial evidence dict to begin descent from.

        Returns
        -------
        dict[str, Any]
            Final evidence dict after all steps, annotated with execution
            metadata under the ``"_descent"`` key.
        """
        plan = self.plan_execution()
        current_evidence = copy.deepcopy(initial_evidence)
        current_trust = 1.0
        local_sections: dict[str, dict] = {}
        all_steps_ok = True

        for step in plan:
            step_evidence_before = copy.deepcopy(current_evidence)
            updated_evidence, success = self.execute_single_step(step, current_evidence)

            if not success:
                all_steps_ok = False
                log_entry: dict[str, Any] = {
                    "step_idx": step["step_idx"],
                    "bridge_id": step["bridge_id"],
                    "status": "failed",
                    "trust_before": current_trust,
                    "trust_after": current_trust,
                }
                self.execution_log.append(log_entry)
                continue

            current_trust = self.propagate_trust(current_trust, step)

            try:
                updated_evidence = self.enforce_kind_preservation(updated_evidence, step)
            except ValueError as exc:
                all_steps_ok = False
                self.execution_log.append({
                    "step_idx": step["step_idx"],
                    "bridge_id": step["bridge_id"],
                    "status": "kind_preservation_error",
                    "error": str(exc),
                    "trust_before": current_trust,
                    "trust_after": current_trust,
                })
                continue

            log_entry = {
                "step_idx": step["step_idx"],
                "bridge_id": step["bridge_id"],
                "status": "ok",
                "trust_before": current_trust / max(step["trust_ceiling"], 1e-9),
                "trust_after": current_trust,
                "keys_transported": [
                    k for k in updated_evidence if not k.startswith("_")
                ],
            }
            self.execution_log.append(log_entry)

            # Record section for source pack
            local_sections[step["source"]] = dict(step_evidence_before)
            current_evidence = updated_evidence

        local_sections[self.protocol.get_bridge_path()[-1] if self.protocol.bridge_sequence else "final"] = dict(current_evidence)

        result = self.assemble_result(local_sections, current_trust)
        result["_descent"] = {
            "steps_executed": len(plan),
            "all_steps_ok": all_steps_ok,
            "final_trust": current_trust,
        }
        return result

    # ------------------------------------------------------------------
    # 3. execute_single_step
    # ------------------------------------------------------------------

    def execute_single_step(
        self, step: dict[str, Any], current_evidence: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Execute a single descent step.

        Looks up the bridge in :attr:`bridge_index`, applies
        :meth:`BridgeTheoremEncoding.apply_to_evidence` to the current
        evidence, and returns the updated evidence together with a success
        flag.  If the bridge is not found, returns the evidence unchanged
        with ``success=False``.

        Parameters
        ----------
        step:
            A step dict from :meth:`plan_execution`.
        current_evidence:
            Evidence dict before this step.

        Returns
        -------
        tuple[dict[str, Any], bool]
            ``(updated_evidence, True)`` on success;
            ``(current_evidence, False)`` if the bridge is not in the index.
        """
        bridge_id = step["bridge_id"]
        bridge = self.bridge_index.get(bridge_id)
        if bridge is None:
            return dict(current_evidence), False

        translated = bridge.apply_to_evidence(current_evidence)
        # Preserve non-overlap keys from current evidence (only lost via overlap whitelist)
        merged = dict(current_evidence)
        for k, v in translated.items():
            merged[k] = v

        # Update step state
        self._state[f"step_{step['step_idx']}"] = {
            "bridge_id": bridge_id,
            "status": "executed",
            "keys_in": list(current_evidence.keys()),
            "keys_out": list(translated.keys()),
        }
        return merged, True

    # ------------------------------------------------------------------
    # 4. propagate_trust
    # ------------------------------------------------------------------

    def propagate_trust(self, current_trust: float, step: dict[str, Any]) -> float:
        """Update the running trust after executing *step*.

        For ``"strict"`` kind_preservation_mode: returns
        ``min(current_trust, step["trust_ceiling"])``.

        For ``"relaxed"`` or ``"advisory"`` modes: returns the harmonic mean
        of *current_trust* and *step["trust_ceiling"]*.

        Parameters
        ----------
        current_trust:
            The trust level before this step.
        step:
            The step dict containing ``"trust_ceiling"``.

        Returns
        -------
        float
            Updated trust value in [0, 1].
        """
        ceiling = step["trust_ceiling"]
        if self.protocol.kind_preservation_mode == "strict":
            return min(current_trust, ceiling)
        # harmonic mean for relaxed / advisory
        if current_trust <= 0.0 or ceiling <= 0.0:
            return 0.0
        return 2.0 / (1.0 / current_trust + 1.0 / ceiling)

    # ------------------------------------------------------------------
    # 5. enforce_kind_preservation
    # ------------------------------------------------------------------

    def enforce_kind_preservation(
        self, evidence: dict[str, Any], step: dict[str, Any]
    ) -> dict[str, Any]:
        """Ensure the "kind" key in *evidence* is preserved after a step.

        In ``"strict"`` mode, if the current evidence contains a ``"kind"``
        key and the ``step`` contains an ``"original_kind"`` override, the
        method raises :class:`ValueError` if they differ.

        In ``"relaxed"`` mode, a drift is logged but allowed.

        In ``"advisory"`` mode, kind drift is silently accepted.

        Parameters
        ----------
        evidence:
            Evidence dict after the descent step.
        step:
            The step dict from the execution plan.

        Returns
        -------
        dict[str, Any]
            Possibly modified evidence dict (with ``"kind"`` reinstated
            in strict mode, or ``"_kind_drift"`` annotation in relaxed mode).

        Raises
        ------
        ValueError
            In ``"strict"`` mode when kind has changed.
        """
        result = dict(evidence)
        original_kind = self._state.get("original_kind") or evidence.get("original_kind")
        current_kind = evidence.get("kind")

        if original_kind is None and current_kind is not None:
            self._state["original_kind"] = current_kind
            result["original_kind"] = current_kind
            return result

        if original_kind is not None and current_kind is not None:
            if current_kind != original_kind:
                msg = (
                    f"Kind changed from {original_kind!r} to {current_kind!r} "
                    f"at step {step.get('step_idx', '?')} (bridge {step.get('bridge_id', '?')!r})"
                )
                if self.protocol.kind_preservation_mode == "strict":
                    raise ValueError(msg)
                elif self.protocol.kind_preservation_mode == "relaxed":
                    result["_kind_drift"] = {
                        "original": original_kind,
                        "current": current_kind,
                        "step": step.get("step_idx"),
                    }
                # advisory: silently continue

        return result

    # ------------------------------------------------------------------
    # 6. resolve_conflict
    # ------------------------------------------------------------------

    def resolve_conflict(
        self,
        evidence_a: dict[str, Any],
        evidence_b: dict[str, Any],
        bridge: BridgeTheoremEncoding,
    ) -> dict[str, Any]:
        """Merge two conflicting evidence records from adjacent packs.

        Iterates over keys in both evidence dicts.  For keys unique to one
        side, includes them unchanged.  For conflicting keys (same key,
        different value), prefer the evidence from the side with higher
        trust (encoded in ``evidence_a.get("_trust", 1.0)`` vs
        ``evidence_b.get("_trust", 1.0)``), and records the conflict in the
        provenance.

        Parameters
        ----------
        evidence_a:
            Evidence dict from the first side.
        evidence_b:
            Evidence dict from the second side.
        bridge:
            The bridge across which the conflict occurred.

        Returns
        -------
        dict[str, Any]
            Merged evidence dict with a ``"_conflict_provenance"`` key listing
            the resolved conflicts.
        """
        trust_a = float(evidence_a.get("_trust", 1.0))
        trust_b = float(evidence_b.get("_trust", 1.0))

        merged: dict[str, Any] = {}
        conflict_provenance: list[dict[str, Any]] = []

        all_keys = (
            set(k for k in evidence_a if not k.startswith("_"))
            | set(k for k in evidence_b if not k.startswith("_"))
        )

        for key in sorted(all_keys):
            has_a = key in evidence_a
            has_b = key in evidence_b
            if has_a and not has_b:
                merged[key] = evidence_a[key]
            elif has_b and not has_a:
                merged[key] = evidence_b[key]
            else:
                val_a = evidence_a[key]
                val_b = evidence_b[key]
                if val_a == val_b:
                    merged[key] = val_a
                else:
                    # Conflict: pick higher trust
                    if trust_a >= trust_b:
                        merged[key] = val_a
                        chosen_side = "a"
                    else:
                        merged[key] = val_b
                        chosen_side = "b"
                    conflict_provenance.append({
                        "key": key,
                        "value_a": val_a,
                        "value_b": val_b,
                        "chosen_side": chosen_side,
                        "trust_a": trust_a,
                        "trust_b": trust_b,
                        "bridge_id": bridge.bridge_id,
                    })

        merged["_trust"] = max(trust_a, trust_b)
        merged["_conflict_provenance"] = conflict_provenance
        return merged

    # ------------------------------------------------------------------
    # 7. assemble_result
    # ------------------------------------------------------------------

    def assemble_result(
        self,
        local_sections: dict[str, dict[str, Any]],
        final_trust: float,
    ) -> dict[str, Any]:
        """Build the final federation result dict from local sections.

        Merges all local sections into a single global result dict,
        attaching protocol metadata, final trust, and a provenance record.

        Parameters
        ----------
        local_sections:
            Dict mapping pack_id to its final local section.
        final_trust:
            The final accumulated trust value.

        Returns
        -------
        dict[str, Any]
            Result dict with keys: ``"local_sections"``, ``"final_trust"``,
            ``"protocol_id"``, ``"kind_preservation_mode"``, ``"trust_floor"``,
            ``"bridge_sequence"``, ``"provenance"``.
        """
        provenance: dict[str, Any] = {
            "protocol_id": self.protocol.protocol_id,
            "bridge_sequence": list(self.protocol.bridge_sequence),
            "participating_packs": sorted(self.protocol.participating_packs),
            "steps_logged": len(self.execution_log),
        }

        return {
            "local_sections": {
                pack_id: dict(section)
                for pack_id, section in local_sections.items()
            },
            "final_trust": final_trust,
            "protocol_id": self.protocol.protocol_id,
            "kind_preservation_mode": self.protocol.kind_preservation_mode,
            "trust_floor": self.protocol.trust_floor,
            "bridge_sequence": list(self.protocol.bridge_sequence),
            "provenance": provenance,
        }

    # ------------------------------------------------------------------
    # 8. validate_result
    # ------------------------------------------------------------------

    def validate_result(self, result: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate a federation result dict.

        Checks that the result contains all required keys, that
        ``final_trust >= protocol.trust_floor``, and that kind is preserved
        if kind_preservation_mode == "strict".

        Parameters
        ----------
        result:
            Result dict produced by :meth:`assemble_result` or
            :meth:`execute_descent`.

        Returns
        -------
        tuple[bool, list[str]]
            ``(True, [])`` if valid; ``(False, errors)`` otherwise.
        """
        errors: list[str] = []
        required_keys = {
            "local_sections", "final_trust", "protocol_id",
            "kind_preservation_mode", "trust_floor", "bridge_sequence",
        }
        for key in sorted(required_keys):
            if key not in result:
                errors.append(f"Missing required key in result: {key!r}")

        if "final_trust" in result:
            ft = result["final_trust"]
            tfloor = self.protocol.trust_floor
            if ft < tfloor:
                errors.append(
                    f"final_trust {ft:.4f} is below protocol trust_floor {tfloor:.4f}"
                )

        if "kind_preservation_mode" in result:
            if result["kind_preservation_mode"] == "strict":
                for pack_id, section in result.get("local_sections", {}).items():
                    kind = section.get("kind")
                    original = section.get("original_kind")
                    if kind is not None and original is not None and kind != original:
                        errors.append(
                            f"kind preservation violated in pack {pack_id!r}: "
                            f"kind={kind!r} != original_kind={original!r}"
                        )

        return len(errors) == 0, errors

    # ------------------------------------------------------------------
    # 9. get_execution_log
    # ------------------------------------------------------------------

    def get_execution_log(self) -> list[dict[str, Any]]:
        """Return a copy of the execution log.

        The execution log accumulates one entry per step executed by
        :meth:`execute_descent`, regardless of success or failure.

        Returns
        -------
        list[dict[str, Any]]
            Shallow copy of :attr:`execution_log`.
        """
        return list(self.execution_log)

    # ------------------------------------------------------------------
    # 10. reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear the execution log and internal state.

        After calling this method, the engine is ready to run a fresh
        execution from scratch.  The :attr:`protocol` and
        :attr:`bridge_index` are not modified.
        """
        self.execution_log.clear()
        self._state.clear()
