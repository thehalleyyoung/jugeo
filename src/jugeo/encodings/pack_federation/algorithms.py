r"""Core algorithms for pack federation.

Theory (theory2.tex §35.4 — Algorithmic Foundations):
    This module provides the core algorithmic primitives that underpin
    the pack federation encoding.  Five primary functions implement the
    fundamental computations of §35.4:

    compute_sheaf_condition (§35.4.1):
        Checks the gluing axiom of the sheaf directly on evidence dicts,
        producing a satisfaction flag and a list of violations.

    find_minimal_bridge_path (§35.4.2):
        Solves the shortest-path problem on the pack connectivity graph
        using BFS, returning an ordered list of bridge IDs that connects
        a source pack to a target pack with the fewest hops.

    compute_federation_trust_ceiling (§35.4.3):
        Implements Lemma 35.7 (monotone trust): the trust ceiling of a
        bridge path is min(trust_ceilings along the path).

    validate_overlap_laws (§35.4.4):
        Checks that two evidence dicts agree on all shared coordinates of a
        :class:`PackBoundary`, implementing the overlap law consistency check.

    assemble_federation_result (§35.4.5):
        Assembles a global section from local sections by threading
        through the bridge path, applying each bridge's evidence translation,
        and building a provenance record.

    Two additional helpers:

    compute_pack_overlap_graph:
        Builds an adjacency list of pack connectivity from a bridge list.

    score_federation_quality:
        Produces a scalar quality score in [0, 1] reflecting trust,
        kind preservation, and completeness.

Public surface
--------------
:func:`compute_sheaf_condition`
:func:`find_minimal_bridge_path`
:func:`compute_federation_trust_ceiling`
:func:`validate_overlap_laws`
:func:`assemble_federation_result`
:func:`compute_pack_overlap_graph`
:func:`score_federation_quality`

copilot: pack-federation-algorithms
"""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, Final, FrozenSet, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from .models import BridgeTheoremEncoding, FederationProtocol, PackBoundary

__all__: list[str] = [
    "compute_sheaf_condition",
    "find_minimal_bridge_path",
    "compute_federation_trust_ceiling",
    "validate_overlap_laws",
    "assemble_federation_result",
    "compute_pack_overlap_graph",
    "score_federation_quality",
]


# ---------------------------------------------------------------------------
# 1. compute_sheaf_condition
# ---------------------------------------------------------------------------


def compute_sheaf_condition(
    packs: Mapping[str, dict],
    bridges: Sequence[BridgeTheoremEncoding],
) -> tuple[bool, list[str]]:
    """Check the sheaf gluing axiom over a collection of packs and bridges.

    For every bridge B in *bridges*, this function checks that the evidence
    dicts of ``B.source_pack_id`` and ``B.target_pack_id`` (looked up from
    *packs*) agree on all coordinates in ``B.overlap_region``.

    Formally: for all k ∈ B.overlap_region,
        packs[B.source_pack_id][k] == packs[B.target_pack_id][k]

    This implements the sheaf gluing axiom of theory2.tex §35.1.

    Parameters
    ----------
    packs:
        Mapping from pack_id to evidence dict (local section).
    bridges:
        Sequence of :class:`BridgeTheoremEncoding` objects specifying the
        morphisms.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if the gluing axiom holds for all bridges;
        ``(False, violations)`` where each violation string describes the
        conflict.

    Examples
    --------
    >>> from jugeo.encodings.pack_federation.models import BridgeTheoremEncoding
    >>> bridge = BridgeTheoremEncoding(
    ...     bridge_id="b1", source_pack_id="A", target_pack_id="B",
    ...     overlap_region=frozenset({"x"}), source_formula="x", target_formula="x",
    ...     trust_ceiling=0.9, morphism_type="injective")
    >>> compute_sheaf_condition({"A": {"x": 1}, "B": {"x": 1}}, [bridge])
    (True, [])
    >>> compute_sheaf_condition({"A": {"x": 1}, "B": {"x": 2}}, [bridge])
    (False, [...])
    """
    violations: list[str] = []

    for bridge in bridges:
        src_section = packs.get(bridge.source_pack_id, {})
        tgt_section = packs.get(bridge.target_pack_id, {})

        if not isinstance(src_section, dict):
            violations.append(
                f"Bridge {bridge.bridge_id!r}: source pack {bridge.source_pack_id!r} "
                f"section is not a dict"
            )
            continue
        if not isinstance(tgt_section, dict):
            violations.append(
                f"Bridge {bridge.bridge_id!r}: target pack {bridge.target_pack_id!r} "
                f"section is not a dict"
            )
            continue

        for coord in sorted(bridge.overlap_region):
            src_has = coord in src_section
            tgt_has = coord in tgt_section

            # Both absent: vacuously ok
            if not src_has and not tgt_has:
                continue

            # One absent, one present: violation
            if src_has and not tgt_has:
                violations.append(
                    f"Bridge {bridge.bridge_id!r}: coord {coord!r} present in "
                    f"source {bridge.source_pack_id!r} but absent from target "
                    f"{bridge.target_pack_id!r}"
                )
                continue
            if tgt_has and not src_has:
                violations.append(
                    f"Bridge {bridge.bridge_id!r}: coord {coord!r} present in "
                    f"target {bridge.target_pack_id!r} but absent from source "
                    f"{bridge.source_pack_id!r}"
                )
                continue

            # Both present: values must agree
            src_val = src_section[coord]
            tgt_val = tgt_section[coord]
            if src_val != tgt_val:
                violations.append(
                    f"Bridge {bridge.bridge_id!r}: coord {coord!r} "
                    f"source={src_val!r} != target={tgt_val!r}"
                )

    return len(violations) == 0, violations


# ---------------------------------------------------------------------------
# 2. find_minimal_bridge_path
# ---------------------------------------------------------------------------


def find_minimal_bridge_path(
    source_pack: str,
    target_pack: str,
    registry: Mapping[str, BridgeTheoremEncoding],
) -> list[str] | None:
    """Find the shortest bridge path from *source_pack* to *target_pack*.

    Treats packs as nodes and bridges as directed edges in a graph, then
    runs BFS to find the shortest sequence of bridge IDs that connects
    *source_pack* to *target_pack*.

    This implements the path-finding sub-algorithm of theory2.tex §35.4.2.

    Parameters
    ----------
    source_pack:
        Pack ID to start from.
    target_pack:
        Pack ID to reach.
    registry:
        Mapping from bridge_id to :class:`BridgeTheoremEncoding`.

    Returns
    -------
    list[str] | None
        Ordered list of bridge_ids forming the shortest directed path from
        *source_pack* to *target_pack*, or ``None`` if no path exists.

    Notes
    -----
    The function traverses bridges in both directions if needed: a bridge
    ``A → B`` can be traversed as ``B → A`` only if it is bijective
    (invertible).
    """
    if source_pack == target_pack:
        return []

    # Build adjacency: pack_id -> list of (bridge_id, next_pack_id)
    adjacency: dict[str, list[tuple[str, str]]] = {}

    for bridge_id, bridge in registry.items():
        src = bridge.source_pack_id
        tgt = bridge.target_pack_id

        adjacency.setdefault(src, []).append((bridge_id, tgt))

        # Allow reverse traversal for bijective bridges
        if bridge.is_bijective():
            adjacency.setdefault(tgt, []).append((bridge_id, src))

    # BFS
    visited: set[str] = {source_pack}
    # queue elements: (current_pack, path_so_far)
    queue: deque[tuple[str, list[str]]] = deque([(source_pack, [])])

    while queue:
        current, path = queue.popleft()
        for bridge_id, next_pack in adjacency.get(current, []):
            if next_pack in visited:
                continue
            new_path = path + [bridge_id]
            if next_pack == target_pack:
                return new_path
            visited.add(next_pack)
            queue.append((next_pack, new_path))

    # No path found
    return None


# ---------------------------------------------------------------------------
# 3. compute_federation_trust_ceiling
# ---------------------------------------------------------------------------


def compute_federation_trust_ceiling(
    bridge_path: Sequence[BridgeTheoremEncoding],
) -> float:
    """Compute the trust ceiling for a bridge path (Lemma 35.7).

    The monotone trust lemma (theory2.tex §35 Lemma 35.7) states that the
    effective trust ceiling of a composed sequence of bridges is the minimum
    of the individual trust ceilings:

        trust(B₁ ∘ B₂ ∘ ... ∘ Bₙ) = min(trust(B₁), trust(B₂), ..., trust(Bₙ))

    This is because trust is a monotone decreasing measure: composing bridges
    can only reduce or preserve trust, never increase it.

    Parameters
    ----------
    bridge_path:
        Sequence of :class:`BridgeTheoremEncoding` objects forming the path.

    Returns
    -------
    float
        The minimum trust ceiling across all bridges in *bridge_path*, or
        ``1.0`` if *bridge_path* is empty.

    Examples
    --------
    >>> from jugeo.encodings.pack_federation.models import BridgeTheoremEncoding
    >>> b1 = BridgeTheoremEncoding("b1", "A", "B", frozenset({"x"}), "x", "x", 0.9, "injective")
    >>> b2 = BridgeTheoremEncoding("b2", "B", "C", frozenset({"x"}), "x", "x", 0.7, "injective")
    >>> compute_federation_trust_ceiling([b1, b2])
    0.7
    """
    if not bridge_path:
        return 1.0
    return min(b.trust_ceiling for b in bridge_path)


# ---------------------------------------------------------------------------
# 4. validate_overlap_laws
# ---------------------------------------------------------------------------


def validate_overlap_laws(
    boundary: PackBoundary,
    evidence_a: dict[str, Any],
    evidence_b: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Validate that two evidence dicts agree on all shared boundary coordinates.

    For each coordinate in ``boundary.shared_coordinates``, checks that
    ``evidence_a[coord] == evidence_b[coord]`` whenever both dicts contain
    that coordinate.  A violation is recorded whenever:
    - Both dicts contain the coordinate but with different values.
    - Only one dict contains the coordinate (asymmetric presence).

    This implements the overlap law consistency check of theory2.tex §35.4.4.

    Parameters
    ----------
    boundary:
        The :class:`PackBoundary` specifying which coordinates are shared.
    evidence_a:
        Evidence dict for ``boundary.pack_a_id``.
    evidence_b:
        Evidence dict for ``boundary.pack_b_id``.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if all shared coordinates agree; ``(False, conflicts)``
        otherwise where each element of *conflicts* describes one conflict.
    """
    conflicts: list[str] = []

    for coord in sorted(boundary.shared_coordinates):
        has_a = coord in evidence_a
        has_b = coord in evidence_b

        if not has_a and not has_b:
            continue

        if has_a and not has_b:
            conflicts.append(
                f"Coord {coord!r}: present in {boundary.pack_a_id!r} "
                f"but absent from {boundary.pack_b_id!r} "
                f"(value={evidence_a[coord]!r})"
            )
            continue

        if has_b and not has_a:
            conflicts.append(
                f"Coord {coord!r}: present in {boundary.pack_b_id!r} "
                f"but absent from {boundary.pack_a_id!r} "
                f"(value={evidence_b[coord]!r})"
            )
            continue

        val_a = evidence_a[coord]
        val_b = evidence_b[coord]
        if val_a != val_b:
            conflicts.append(
                f"Coord {coord!r}: {boundary.pack_a_id!r} has {val_a!r} "
                f"but {boundary.pack_b_id!r} has {val_b!r}"
            )

    return len(conflicts) == 0, conflicts


# ---------------------------------------------------------------------------
# 5. assemble_federation_result
# ---------------------------------------------------------------------------


def assemble_federation_result(
    local_sections: Mapping[str, dict],
    bridge_path: Sequence[BridgeTheoremEncoding],
    protocol: FederationProtocol,
) -> dict[str, Any]:
    """Assemble a global federation result from local sections via a bridge path.

    Threads the local sections through the bridge path, applying each
    bridge's :meth:`~BridgeTheoremEncoding.apply_to_evidence` in order,
    accumulating trust via the monotone trust lemma, and building a
    provenance record.

    Steps:
    1. Start with the evidence from the first bridge's source pack.
    2. For each bridge in *bridge_path*, apply ``bridge.apply_to_evidence``
       to the running evidence, restricting to the overlap region.
    3. Update running trust via ``min(running_trust, bridge.trust_ceiling)``.
    4. Record each bridge step in the provenance.
    5. Assemble the final result dict.

    Parameters
    ----------
    local_sections:
        Mapping from pack_id to evidence dict.
    bridge_path:
        Ordered sequence of :class:`BridgeTheoremEncoding` objects.
    protocol:
        The :class:`FederationProtocol` governing this assembly.

    Returns
    -------
    dict[str, Any]
        Global result dict with keys: ``"evidence"``, ``"final_trust"``,
        ``"protocol_id"``, ``"provenance"``, ``"local_sections"``.
    """
    provenance: list[dict[str, Any]] = []
    running_trust = 1.0

    if not bridge_path:
        # No bridges: return merged local sections directly
        merged: dict[str, Any] = {}
        for section in local_sections.values():
            for k, v in section.items():
                if not k.startswith("_"):
                    merged[k] = v
        return {
            "evidence": merged,
            "final_trust": running_trust,
            "protocol_id": protocol.protocol_id,
            "provenance": provenance,
            "local_sections": dict(local_sections),
        }

    # Start from the source of the first bridge
    first_bridge = bridge_path[0]
    current_evidence: dict[str, Any] = dict(
        local_sections.get(first_bridge.source_pack_id, {})
    )

    for bridge in bridge_path:
        # Apply bridge translation
        translated = bridge.apply_to_evidence(current_evidence)

        # Update trust (Lemma 35.7)
        running_trust = min(running_trust, bridge.trust_ceiling)

        # Record provenance
        provenance.append({
            "bridge_id": bridge.bridge_id,
            "source": bridge.source_pack_id,
            "target": bridge.target_pack_id,
            "trust_after": running_trust,
            "coords_transported": sorted(
                k for k in translated if not k.startswith("_")
            ),
        })

        # Merge translated into current evidence, preserving non-overlap keys
        for k, v in translated.items():
            current_evidence[k] = v

        # Also pull in the target pack's local section for new coordinates
        target_section = local_sections.get(bridge.target_pack_id, {})
        for k, v in target_section.items():
            if not k.startswith("_") and k not in current_evidence:
                current_evidence[k] = v

    return {
        "evidence": {k: v for k, v in current_evidence.items() if not k.startswith("_")},
        "final_trust": running_trust,
        "protocol_id": protocol.protocol_id,
        "provenance": provenance,
        "local_sections": dict(local_sections),
    }


# ---------------------------------------------------------------------------
# 6. compute_pack_overlap_graph
# ---------------------------------------------------------------------------


def compute_pack_overlap_graph(
    bridges: Sequence[BridgeTheoremEncoding],
) -> dict[str, list[str]]:
    """Build an adjacency list of pack connectivity from a bridge sequence.

    Constructs an undirected adjacency list where each pack appears as a key
    and maps to the list of packs it is connected to via at least one bridge.
    Bijective bridges contribute edges in both directions; all other bridge
    types contribute a directed edge from source to target.

    Parameters
    ----------
    bridges:
        Sequence of :class:`BridgeTheoremEncoding` objects.

    Returns
    -------
    dict[str, list[str]]
        Adjacency list: ``pack_id -> [connected_pack_id, ...]``.

    Notes
    -----
    Duplicate entries are removed so that each neighbour appears at most once.
    """
    adjacency: dict[str, set[str]] = {}

    for bridge in bridges:
        src = bridge.source_pack_id
        tgt = bridge.target_pack_id

        adjacency.setdefault(src, set()).add(tgt)

        if bridge.is_bijective():
            adjacency.setdefault(tgt, set()).add(src)
        else:
            # Ensure target appears as a key even if it has no outgoing edges
            adjacency.setdefault(tgt, set())

    return {pack: sorted(neighbours) for pack, neighbours in sorted(adjacency.items())}


# ---------------------------------------------------------------------------
# 7. score_federation_quality
# ---------------------------------------------------------------------------


def score_federation_quality(
    result: dict[str, Any],
    protocol: FederationProtocol,
) -> float:
    """Score the quality of a federation result in [0, 1].

    The quality score is a weighted combination of three sub-scores:

    1. **Trust score** (weight 0.5): ``final_trust / 1.0``, capped at 1.
    2. **Kind preservation score** (weight 0.3): 1.0 if kind is preserved
       across all local sections (``kind == original_kind``), 0.0 if any
       violation is found.
    3. **Completeness score** (weight 0.2): fraction of participating packs
       that have a non-empty local section in the result.

    Parameters
    ----------
    result:
        Federation result dict (as produced by :func:`assemble_federation_result`
        or :meth:`~federation_protocol.FederationProtocolEngine.assemble_result`).
    protocol:
        The :class:`FederationProtocol` that produced the result.

    Returns
    -------
    float
        Quality score in [0, 1].
    """
    # 1. Trust score
    final_trust = float(result.get("final_trust", 0.0))
    trust_score = min(1.0, max(0.0, final_trust))

    # 2. Kind preservation score
    kind_ok = True
    local_sections = result.get("local_sections", {})
    for section in local_sections.values():
        kind = section.get("kind")
        original_kind = section.get("original_kind")
        if kind is not None and original_kind is not None:
            if kind != original_kind:
                kind_ok = False
                break
    kind_score = 1.0 if kind_ok else 0.0

    # Adjust kind score weight based on protocol mode
    if protocol.kind_preservation_mode == "advisory":
        kind_score = 1.0  # advisory mode: kind preservation not penalised

    # 3. Completeness score
    participating = protocol.participating_packs
    if participating:
        covered = sum(
            1 for pid in participating
            if local_sections.get(pid)  # non-empty section
        )
        completeness_score = covered / len(participating)
    else:
        completeness_score = 1.0

    # Weighted combination
    quality = (
        0.5 * trust_score
        + 0.3 * kind_score
        + 0.2 * completeness_score
    )
    return min(1.0, max(0.0, quality))


# ---------------------------------------------------------------------------
# Judgment-geometric cross-references
# ---------------------------------------------------------------------------

try:
    from jugeo.packs import catalog as _packs_catalog
except ImportError:
    _packs_catalog = None  # type: ignore[assignment]

try:
    from jugeo.packs import federation as _packs_federation
except ImportError:
    _packs_federation = None  # type: ignore[assignment]


def pack_encoding(pack: Any) -> dict[str, Any]:
    """Encode a pack from the pack catalog into a federation-ready form.

    Bridges the packs catalog subsystem into the federation pipeline
    by converting a pack object into an encoding that the federation
    protocol can process.

    Parameters
    ----------
    pack:
        A pack object from ``jugeo.packs.catalog``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"pack"``, ``"pack_id"``, and ``"evidence"`` keys.
    """
    if _packs_catalog is None:
        raise RuntimeError("jugeo.packs.catalog is not available")
    pack_id = _packs_catalog.pack_id(pack) if hasattr(_packs_catalog, "pack_id") else str(id(pack))
    evidence = _packs_catalog.evidence(pack) if hasattr(_packs_catalog, "evidence") else {}
    return {
        "pack": pack,
        "pack_id": pack_id,
        "evidence": evidence,
    }


def federated_encoding(encodings: Any) -> dict[str, Any]:
    """Combine multiple encodings into a federated result.

    Uses the packs federation subsystem to merge a collection of
    individual pack encodings into a single federated encoding.

    Parameters
    ----------
    encodings:
        A sequence of pack encodings to federate.

    Returns
    -------
    dict[str, Any]
        A dict with ``"encodings"``, ``"federated"``, and
        ``"participant_count"`` keys.
    """
    if _packs_federation is None:
        raise RuntimeError("jugeo.packs.federation is not available")
    enc_list = list(encodings) if not isinstance(encodings, list) else encodings
    merged = _packs_federation.merge(enc_list) if hasattr(_packs_federation, "merge") else {}
    return {
        "encodings": enc_list,
        "federated": merged,
        "participant_count": len(enc_list),
    }
