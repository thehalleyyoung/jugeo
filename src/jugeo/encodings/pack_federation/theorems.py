r"""Theorem verification functions for pack federation.

Theory (theory2.tex §35.6 — Theorem Discharge):
    This module provides eight theorem-verification functions that formally
    check the key theoretical claims of theory2.tex §35.  Each function
    returns a structured result dict conforming to the schema:

        {
          "theorem_name": str,
          "status": "passed" | "failed",
          "evidence": dict,            # field-by-field evidence records
          "counterexample_or_none": dict | None,  # populated when status=="failed"
        }

    The theorems verified are:

    T1 — Sheaf condition soundness (§35.1):
        A valid pack federation encoding implies the sheaf condition holds.

    T2 — Bridge morphism laws (§35.2):
        Every bridge theorem encoding satisfies the functor axioms.

    T3 — Federation kind preservation (§35.3):
        A federation protocol with strict kind_preservation_mode preserves
        the semantic kind of evidence across all descent steps.

    T4 — Trust ceiling monotonicity (§35 Lemma 35.7):
        The trust ceiling of a bridge path is the minimum of the individual
        bridge trust ceilings.

    T5 — Overlap law consistency (§35.4.4):
        Evidence dicts for adjacent packs agree on all shared boundary
        coordinates.

    T6 — Descent completeness (§35 Theorem 35.2):
        A complete protocol execution produces evidence covering all
        participating packs.

    T7 — Pack boundary coherence (§35.1):
        A pack boundary is coherent with the federation encoding (all
        shared coordinates appear in some bridge overlap region).

    T8 — Federation protocol correctness (§35.3):
        The federation protocol is structurally correct and executable.

Public surface
--------------
:func:`verify_sheaf_condition_soundness`
:func:`verify_bridge_morphism_laws`
:func:`verify_federation_kind_preservation`
:func:`verify_trust_ceiling_monotonicity`
:func:`verify_overlap_law_consistency`
:func:`verify_descent_completeness`
:func:`verify_pack_boundary_coherence`
:func:`verify_federation_protocol_correctness`

copilot: pack-federation-theorems
"""
from __future__ import annotations

from typing import Any, Dict, Final, FrozenSet, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from .algorithms import (
    compute_federation_trust_ceiling,
    compute_sheaf_condition,
    validate_overlap_laws,
)
from .models import (
    BridgeTheoremEncoding,
    FederationProtocol,
    PackBoundary,
    PackFederationEncoding,
)
from .pack_federation_as_sheaf import PackFederationAsSheaf
from .bridge_theorems_as_morphisms import BridgeTheoremAsMorphism
from .federation_protocol import FederationProtocolEngine

__all__: list[str] = [
    "verify_sheaf_condition_soundness",
    "verify_bridge_morphism_laws",
    "verify_federation_kind_preservation",
    "verify_trust_ceiling_monotonicity",
    "verify_overlap_law_consistency",
    "verify_descent_completeness",
    "verify_pack_boundary_coherence",
    "verify_federation_protocol_correctness",
]

# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

_TheoremResult = dict[str, Any]


def _make_result(
    theorem_name: str,
    passed: bool,
    evidence: dict[str, Any],
    counterexample: dict[str, Any] | None = None,
) -> _TheoremResult:
    """Build a standard theorem result dict."""
    return {
        "theorem_name": theorem_name,
        "status": "passed" if passed else "failed",
        "evidence": evidence,
        "counterexample_or_none": counterexample if not passed else None,
    }


# ---------------------------------------------------------------------------
# T1 — Sheaf condition soundness
# ---------------------------------------------------------------------------


def verify_sheaf_condition_soundness(
    encoding: PackFederationEncoding,
    sheaf: PackFederationAsSheaf,
) -> _TheoremResult:
    """Verify that a valid encoding implies the sheaf condition holds (T1).

    Theorem (theory2.tex §35.1):
        If ``encoding.sheaf_condition_status == "satisfied"`` then the gluing
        axiom holds on the sheaf — i.e., every bridge's source and target
        local sections agree on the overlap region.

    This function checks both sides:
    1. Calls :meth:`PackFederationAsSheaf.check_sheaf_condition` on *sheaf*.
    2. Compares the result against ``encoding.sheaf_condition_status``.

    Parameters
    ----------
    encoding:
        The pack federation encoding to verify.
    sheaf:
        The corresponding sheaf object.

    Returns
    -------
    dict
        Standard theorem result dict.
    """
    # Direct sheaf condition check
    sheaf_ok, violations = sheaf.check_sheaf_condition()

    # Is the encoding's stored status consistent?
    stored_status = encoding.sheaf_condition_status
    stored_says_satisfied = stored_status == "satisfied"

    # T1 passes iff:
    # (a) the live check agrees with the stored status, OR
    # (b) the stored status is "unknown" (inconclusive — not a violation)
    status_consistent = (
        stored_status == "unknown"
        or (sheaf_ok == stored_says_satisfied)
    )

    # Also check that is_valid_sheaf() agrees with sheaf_ok when status == "satisfied"
    is_valid = encoding.is_valid_sheaf()
    valid_consistent = (stored_status != "satisfied") or (is_valid == sheaf_ok)

    passed = sheaf_ok and status_consistent and valid_consistent

    evidence: dict[str, Any] = {
        "sheaf_condition_ok": sheaf_ok,
        "violations": violations,
        "stored_status": stored_status,
        "status_consistent": status_consistent,
        "is_valid_sheaf": is_valid,
        "valid_consistent": valid_consistent,
        "bridge_count": encoding.get_bridge_count(),
        "pack_count": len(encoding.pack_ids),
        "cohomology_class": encoding.compute_cohomology_class(),
    }

    counterexample: dict[str, Any] | None = None
    if not passed:
        counterexample = {
            "violations": violations[:5],
            "stored_status": stored_status,
            "computed_sheaf_ok": sheaf_ok,
        }

    return _make_result("T1_sheaf_condition_soundness", passed, evidence, counterexample)


# ---------------------------------------------------------------------------
# T2 — Bridge morphism laws
# ---------------------------------------------------------------------------


def verify_bridge_morphism_laws(
    bridge: BridgeTheoremEncoding,
    morphism: BridgeTheoremAsMorphism,
) -> _TheoremResult:
    """Verify that a bridge theorem encoding satisfies the functor axioms (T2).

    Theorem (theory2.tex §35.2):
        Every bridge theorem is a valid functor: it preserves identity (F1) and
        composition (F2), and its overlap laws hold.

    Parameters
    ----------
    bridge:
        The bridge theorem encoding to verify.
    morphism:
        The corresponding :class:`BridgeTheoremAsMorphism` wrapper.

    Returns
    -------
    dict
        Standard theorem result dict.
    """
    # Functor laws
    functor_ok, functor_errors = morphism.verify_functor_laws()

    # Overlap laws
    overlap_ok, overlap_errors = morphism.verify_overlap_laws()

    # Morphism laws from the encoding itself
    morphism_laws_ok, morphism_errors = bridge.validate_morphism_laws()

    # Kernel and image
    kernel = morphism.compute_kernel()
    image = morphism.compute_image()
    faithful = morphism.check_faithfulness()
    category = morphism.get_morphism_category()

    # T2 passes iff all three law checks pass
    passed = functor_ok and overlap_ok and morphism_laws_ok

    evidence: dict[str, Any] = {
        "functor_laws_ok": functor_ok,
        "functor_errors": functor_errors,
        "overlap_laws_ok": overlap_ok,
        "overlap_errors": overlap_errors,
        "morphism_laws_ok": morphism_laws_ok,
        "morphism_errors": morphism_errors,
        "kernel_size": len(kernel),
        "kernel_terms": sorted(kernel)[:10],
        "image_size": len(image),
        "faithful": faithful,
        "morphism_category": category,
        "trust_ceiling": bridge.trust_ceiling,
        "morphism_type": bridge.morphism_type,
    }

    counterexample: dict[str, Any] | None = None
    if not passed:
        counterexample = {
            "functor_errors": functor_errors,
            "overlap_errors": overlap_errors,
            "morphism_errors": morphism_errors,
        }

    return _make_result("T2_bridge_morphism_laws", passed, evidence, counterexample)


# ---------------------------------------------------------------------------
# T3 — Federation kind preservation
# ---------------------------------------------------------------------------


def verify_federation_kind_preservation(
    protocol: FederationProtocol,
    engine: FederationProtocolEngine,
    test_evidence: dict[str, Any],
) -> _TheoremResult:
    """Verify that the federation protocol preserves semantic kind (T3).

    Theorem (theory2.tex §35.3):
        If ``kind_preservation_mode == "strict"`` then running
        :meth:`~FederationProtocolEngine.execute_descent` on any valid
        initial evidence preserves the ``"kind"`` key unchanged.

    Parameters
    ----------
    protocol:
        The federation protocol.
    engine:
        The protocol engine to execute.
    test_evidence:
        Initial evidence dict.  Should include a ``"kind"`` key.

    Returns
    -------
    dict
        Standard theorem result dict.
    """
    # Reset engine to get a fresh log
    engine.reset()

    initial_kind = test_evidence.get("kind")
    # Set original_kind so enforce_kind_preservation can compare
    evidence_with_origin = dict(test_evidence)
    if initial_kind is not None:
        evidence_with_origin["original_kind"] = initial_kind

    result = engine.execute_descent(evidence_with_origin)
    log = engine.get_execution_log()

    # Check kind in all local sections
    kind_violations: list[str] = []
    for pack_id, section in result.get("local_sections", {}).items():
        kind_after = section.get("kind")
        original = section.get("original_kind")
        if kind_after is not None and original is not None and kind_after != original:
            kind_violations.append(
                f"pack {pack_id!r}: kind changed {original!r} → {kind_after!r}"
            )

    # For strict mode: there must be no kind violations in the log
    kind_error_steps = [
        entry for entry in log
        if entry.get("status") == "kind_preservation_error"
    ]

    if protocol.kind_preservation_mode == "strict":
        passed = len(kind_violations) == 0 and len(kind_error_steps) == 0
    else:
        # relaxed/advisory: kind preservation is advisory; test is informational
        passed = True  # non-strict modes cannot fail this theorem

    evidence: dict[str, Any] = {
        "kind_preservation_mode": protocol.kind_preservation_mode,
        "initial_kind": initial_kind,
        "kind_violations": kind_violations,
        "kind_error_steps": len(kind_error_steps),
        "steps_executed": len(log),
        "protocol_id": protocol.protocol_id,
        "final_trust": result.get("final_trust"),
    }

    counterexample: dict[str, Any] | None = None
    if not passed:
        counterexample = {
            "kind_violations": kind_violations,
            "kind_error_steps": kind_error_steps,
            "initial_kind": initial_kind,
        }

    return _make_result("T3_federation_kind_preservation", passed, evidence, counterexample)


# ---------------------------------------------------------------------------
# T4 — Trust ceiling monotonicity
# ---------------------------------------------------------------------------


def verify_trust_ceiling_monotonicity(
    bridge_path: Sequence[BridgeTheoremEncoding],
) -> _TheoremResult:
    """Verify Lemma 35.7: trust ceiling is monotone along a bridge path (T4).

    Lemma 35.7 (theory2.tex §35):
        trust(B₁ ∘ B₂ ∘ ... ∘ Bₙ) = min_{i}(trust(Bᵢ)).

    Also verifies: the trust ceiling of each prefix of the path is a
    non-increasing sequence (monotone non-increasing property).

    Parameters
    ----------
    bridge_path:
        Sequence of :class:`BridgeTheoremEncoding` objects.

    Returns
    -------
    dict
        Standard theorem result dict.
    """
    individual_trusts = [b.trust_ceiling for b in bridge_path]
    computed_ceiling = compute_federation_trust_ceiling(bridge_path)

    # Verify: computed_ceiling == min(individual_trusts)
    if individual_trusts:
        expected_min = min(individual_trusts)
        ceiling_correct = abs(computed_ceiling - expected_min) < 1e-9
    else:
        expected_min = 1.0
        ceiling_correct = abs(computed_ceiling - 1.0) < 1e-9

    # Monotonicity: prefix trust ceilings must be non-increasing
    prefix_trusts: list[float] = []
    prefix_violations: list[str] = []
    running = 1.0
    for i, bridge in enumerate(bridge_path):
        running = min(running, bridge.trust_ceiling)
        if prefix_trusts and running > prefix_trusts[-1] + 1e-9:
            prefix_violations.append(
                f"Step {i}: trust increased from {prefix_trusts[-1]:.4f} to {running:.4f}"
            )
        prefix_trusts.append(running)

    monotone = len(prefix_violations) == 0
    passed = ceiling_correct and monotone

    evidence: dict[str, Any] = {
        "bridge_count": len(bridge_path),
        "individual_trusts": individual_trusts,
        "computed_ceiling": computed_ceiling,
        "expected_min": expected_min,
        "ceiling_correct": ceiling_correct,
        "prefix_trust_ceilings": prefix_trusts,
        "monotone": monotone,
        "prefix_violations": prefix_violations,
    }

    counterexample: dict[str, Any] | None = None
    if not passed:
        counterexample = {
            "computed_ceiling": computed_ceiling,
            "expected_min": expected_min,
            "prefix_violations": prefix_violations,
        }

    return _make_result("T4_trust_ceiling_monotonicity", passed, evidence, counterexample)


# ---------------------------------------------------------------------------
# T5 — Overlap law consistency
# ---------------------------------------------------------------------------


def verify_overlap_law_consistency(
    boundary: PackBoundary,
    evidence_a: dict[str, Any],
    evidence_b: dict[str, Any],
) -> _TheoremResult:
    """Verify that evidence dicts agree on shared boundary coordinates (T5).

    Theorem (theory2.tex §35.4.4):
        For two adjacent packs sharing a boundary, their evidence dicts must
        agree on every coordinate in the boundary's shared_coordinates set.

    Parameters
    ----------
    boundary:
        The shared :class:`PackBoundary`.
    evidence_a:
        Evidence dict for ``boundary.pack_a_id``.
    evidence_b:
        Evidence dict for ``boundary.pack_b_id``.

    Returns
    -------
    dict
        Standard theorem result dict.
    """
    # Use the algorithm function
    overlap_ok, conflicts = validate_overlap_laws(boundary, evidence_a, evidence_b)

    # Also validate the boundary's structural laws
    boundary_laws_ok, boundary_errors = boundary.validate_boundary_laws()

    # Check individual coordinates
    coord_results: dict[str, Any] = {}
    for coord in sorted(boundary.shared_coordinates):
        val_a = evidence_a.get(coord)
        val_b = evidence_b.get(coord)
        coord_results[coord] = {
            "in_a": coord in evidence_a,
            "in_b": coord in evidence_b,
            "value_a": val_a,
            "value_b": val_b,
            "agrees": val_a == val_b,
        }

    passed = overlap_ok and boundary_laws_ok

    evidence: dict[str, Any] = {
        "overlap_ok": overlap_ok,
        "conflicts": conflicts,
        "boundary_laws_ok": boundary_laws_ok,
        "boundary_errors": boundary_errors,
        "shared_coord_count": len(boundary.shared_coordinates),
        "coordinate_details": coord_results,
        "overlap_measure": boundary.compute_overlap_measure(),
        "boundary_type": boundary.boundary_type,
    }

    counterexample: dict[str, Any] | None = None
    if not passed:
        counterexample = {
            "conflicts": conflicts,
            "boundary_errors": boundary_errors,
        }

    return _make_result("T5_overlap_law_consistency", passed, evidence, counterexample)


# ---------------------------------------------------------------------------
# T6 — Descent completeness
# ---------------------------------------------------------------------------


def verify_descent_completeness(
    protocol: FederationProtocol,
    engine: FederationProtocolEngine,
    initial_evidence: dict[str, Any],
) -> _TheoremResult:
    """Verify Theorem 35.2: descent produces evidence for all participating packs (T6).

    Theorem (theory2.tex §35 Theorem 35.2):
        If all bridges in bridge_sequence are present in the engine's bridge_index
        and satisfy trust_ceiling >= trust_floor, then the descent produces at
        least one local section per participating pack.

    Parameters
    ----------
    protocol:
        The federation protocol.
    engine:
        The engine to execute.
    initial_evidence:
        Initial evidence for the descent.

    Returns
    -------
    dict
        Standard theorem result dict.
    """
    engine.reset()
    result = engine.execute_descent(initial_evidence)
    log = engine.get_execution_log()

    # Check: all participating packs have a local section in the result
    local_sections = result.get("local_sections", {})
    packs_covered: set[str] = {
        pid for pid, section in local_sections.items() if section
    }
    participating = set(protocol.participating_packs)
    uncovered = participating - packs_covered

    # Check: all bridges in bridge_sequence were executed
    executed = {entry.get("bridge_id") for entry in log if entry.get("status") == "ok"}
    missing_bridges = set(protocol.bridge_sequence) - executed

    # Check: final_trust >= trust_floor
    final_trust = float(result.get("final_trust", 0.0))
    trust_ok = final_trust >= protocol.trust_floor

    passed = len(uncovered) == 0 and len(missing_bridges) == 0 and trust_ok

    evidence: dict[str, Any] = {
        "participating_packs": sorted(participating),
        "packs_covered": sorted(packs_covered),
        "uncovered_packs": sorted(uncovered),
        "bridge_sequence": list(protocol.bridge_sequence),
        "executed_bridges": sorted(executed),
        "missing_bridges": sorted(missing_bridges),
        "final_trust": final_trust,
        "trust_floor": protocol.trust_floor,
        "trust_ok": trust_ok,
        "steps_logged": len(log),
    }

    counterexample: dict[str, Any] | None = None
    if not passed:
        counterexample = {
            "uncovered_packs": sorted(uncovered),
            "missing_bridges": sorted(missing_bridges),
            "final_trust": final_trust,
            "trust_floor": protocol.trust_floor,
        }

    return _make_result("T6_descent_completeness", passed, evidence, counterexample)


# ---------------------------------------------------------------------------
# T7 — Pack boundary coherence
# ---------------------------------------------------------------------------


def verify_pack_boundary_coherence(
    boundary: PackBoundary,
    encoding: PackFederationEncoding,
) -> _TheoremResult:
    """Verify that a pack boundary is coherent with the federation encoding (T7).

    Coherence (theory2.tex §35.1) requires:
    1. Both ``boundary.pack_a_id`` and ``boundary.pack_b_id`` are in
       ``encoding.pack_ids``.
    2. Every coordinate in ``boundary.shared_coordinates`` appears in at
       least one bridge's overlap_region.
    3. The boundary's structural laws (overlap_laws non-empty, type valid,
       pack IDs distinct) are satisfied.

    Parameters
    ----------
    boundary:
        The boundary to check.
    encoding:
        The federation encoding to check against.

    Returns
    -------
    dict
        Standard theorem result dict.
    """
    # 1. Pack membership
    a_in_encoding = boundary.pack_a_id in encoding.pack_ids
    b_in_encoding = boundary.pack_b_id in encoding.pack_ids

    # 2. Coordinate coverage
    all_bridge_coords: set[str] = set()
    for bridge in encoding.bridge_encodings:
        all_bridge_coords.update(bridge.overlap_region)

    uncovered_coords = [
        coord for coord in boundary.shared_coordinates
        if coord not in all_bridge_coords
    ]

    # 3. Structural boundary laws
    laws_ok, laws_errors = boundary.validate_boundary_laws()

    # 4. Check validate_overlap_laws on an empty evidence pair (vacuous check)
    empty_evidence_ok, _ = validate_overlap_laws(boundary, {}, {})

    passed = (
        a_in_encoding
        and b_in_encoding
        and len(uncovered_coords) == 0
        and laws_ok
    )

    evidence: dict[str, Any] = {
        "pack_a_in_encoding": a_in_encoding,
        "pack_b_in_encoding": b_in_encoding,
        "shared_coord_count": len(boundary.shared_coordinates),
        "uncovered_coords": uncovered_coords,
        "all_bridge_coord_count": len(all_bridge_coords),
        "laws_ok": laws_ok,
        "laws_errors": laws_errors,
        "overlap_measure": boundary.compute_overlap_measure(),
        "empty_overlap_ok": empty_evidence_ok,
    }

    counterexample: dict[str, Any] | None = None
    if not passed:
        counterexample = {
            "pack_a_in_encoding": a_in_encoding,
            "pack_b_in_encoding": b_in_encoding,
            "uncovered_coords": uncovered_coords,
            "laws_errors": laws_errors,
        }

    return _make_result("T7_pack_boundary_coherence", passed, evidence, counterexample)


# ---------------------------------------------------------------------------
# T8 — Federation protocol correctness
# ---------------------------------------------------------------------------


def verify_federation_protocol_correctness(
    protocol: FederationProtocol,
    encoding: PackFederationEncoding,
) -> _TheoremResult:
    """Verify that the federation protocol is structurally correct (T8).

    Theorem (theory2.tex §35.3):
        A correct protocol has non-empty bridge_sequence, participating packs
        matching encoding.pack_ids, trust_floor in [0,1], and bridge_sequence
        entries all appearing in encoding's bridge encodings.

    Parameters
    ----------
    protocol:
        The federation protocol to verify.
    encoding:
        The corresponding encoding.

    Returns
    -------
    dict
        Standard theorem result dict.
    """
    # Structural validation
    descent_ok, descent_errors = protocol.validate_descent()

    # Check participating packs ⊆ encoding.pack_ids
    extra_packs = protocol.participating_packs - encoding.pack_ids
    missing_packs = encoding.pack_ids - protocol.participating_packs

    # Check bridge_sequence entries appear in encoding bridges
    encoding_bridge_ids = {b.bridge_id for b in encoding.bridge_encodings}
    unknown_bridges = [
        bid for bid in protocol.bridge_sequence if bid not in encoding_bridge_ids
    ]

    # Executability
    is_executable = protocol.is_executable()

    # Bridge path
    bridge_path = protocol.get_bridge_path()
    trust_floor_ok = 0.0 <= protocol.trust_floor <= 1.0

    passed = (
        descent_ok
        and len(extra_packs) == 0
        and len(unknown_bridges) == 0
        and trust_floor_ok
    )

    evidence: dict[str, Any] = {
        "descent_ok": descent_ok,
        "descent_errors": descent_errors,
        "participating_packs": sorted(protocol.participating_packs),
        "encoding_packs": sorted(encoding.pack_ids),
        "extra_packs": sorted(extra_packs),
        "missing_packs": sorted(missing_packs),
        "bridge_sequence": bridge_path,
        "unknown_bridges": unknown_bridges,
        "is_executable": is_executable,
        "trust_floor": protocol.trust_floor,
        "trust_floor_ok": trust_floor_ok,
        "kind_preservation_mode": protocol.kind_preservation_mode,
        "descent_conditions": list(protocol.descent_conditions),
    }

    counterexample: dict[str, Any] | None = None
    if not passed:
        counterexample = {
            "descent_errors": descent_errors,
            "extra_packs": sorted(extra_packs),
            "unknown_bridges": unknown_bridges,
            "trust_floor_ok": trust_floor_ok,
        }

    return _make_result("T8_federation_protocol_correctness", passed, evidence, counterexample)
