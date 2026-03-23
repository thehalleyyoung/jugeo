r"""Core data models for the pack_federation encoding.

Theory (theory2.tex §35 — Pack Federation as Sheaves):
    Four frozen dataclasses capture the structural elements of pack
    federation theory.

    - :class:`BridgeTheoremEncoding` encodes a single bridge theorem as a
      morphism between two pack vocabularies, carrying source/target formulas,
      an overlap region (the shared vocabulary), a trust ceiling, and a
      morphism type drawn from {injective, surjective, bijective, partial}.

    - :class:`PackFederationEncoding` encodes an entire federation of packs as
      a sheaf: the set of participating pack IDs, the bridge encodings forming
      the cover morphisms, the federation protocol reference, and the
      sheaf-condition status as computed by the algorithms module.

    - :class:`FederationProtocol` encodes a descent protocol as an ordered
      sequence of bridge IDs together with trust-floor and kind-preservation
      mode.  Executing the protocol simulates descent across pack boundaries.

    - :class:`PackBoundary` encodes the boundary between two adjacent packs,
      listing shared coordinates and overlap laws that must hold across the
      boundary for the sheaf condition to be satisfied.

    §35 Lemma 35.3 (Transitivity of trust):
        If bridge B1 has trust ceiling t1 and B2 has ceiling t2, and B1
        composes into B2, the composed bridge has ceiling min(t1, t2).

Public surface
--------------
:class:`BridgeTheoremEncoding`
    Frozen dataclass for a single bridge morphism.
:class:`PackFederationEncoding`
    Frozen dataclass for a full pack federation.
:class:`FederationProtocol`
    Frozen dataclass for a federation descent protocol.
:class:`PackBoundary`
    Frozen dataclass for a shared pack boundary.

copilot: pack-federation-models
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Final, FrozenSet, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

__all__: list[str] = [
    "BridgeTheoremEncoding",
    "PackFederationEncoding",
    "FederationProtocol",
    "PackBoundary",
]

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_VALID_MORPHISM_TYPES: Final[frozenset[str]] = frozenset(
    {"injective", "surjective", "bijective", "partial"}
)
_VALID_SHEAF_STATUSES: Final[frozenset[str]] = frozenset(
    {"satisfied", "violated", "unknown"}
)
_VALID_KPM: Final[frozenset[str]] = frozenset({"strict", "relaxed", "advisory"})
_VALID_BOUNDARY_TYPES: Final[frozenset[str]] = frozenset(
    {"interior", "frontier", "external"}
)


# ---------------------------------------------------------------------------
# BridgeTheoremEncoding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BridgeTheoremEncoding:
    """Frozen encoding of a single bridge theorem as a categorical morphism.

    A bridge theorem in theory2.tex §35.2 asserts that two packs share a
    common vocabulary fragment (the *overlap region*) and that evidence
    translated through the bridge from the source pack retains its semantic
    content in the target pack up to the stated *trust ceiling*.

    The morphism type classifies the bridge's coverage properties:
    ``"bijective"`` bridges can be inverted; ``"injective"`` bridges are
    one-to-one on the overlap; ``"surjective"`` bridges cover the full target
    overlap; ``"partial"`` bridges are neither injective nor surjective.

    Parameters
    ----------
    bridge_id:
        Unique identifier for this bridge.
    source_pack_id:
        Identifier of the source pack.
    target_pack_id:
        Identifier of the target pack.
    overlap_region:
        Frozen set of coordinate names shared by both packs.
    source_formula:
        Logical formula describing how the source pack encodes the overlap.
    target_formula:
        Logical formula describing how the target pack encodes the overlap.
    trust_ceiling:
        Maximum trust [0.0, 1.0] that evidence can carry after crossing this bridge.
    morphism_type:
        One of ``"injective"``, ``"surjective"``, ``"bijective"``, ``"partial"``.

    copilot: bridge-theorem-encoding
    """

    bridge_id: str
    source_pack_id: str
    target_pack_id: str
    overlap_region: FrozenSet[str]
    source_formula: str
    target_formula: str
    trust_ceiling: float
    morphism_type: str

    # ------------------------------------------------------------------
    # Predicate helpers
    # ------------------------------------------------------------------

    def is_injective(self) -> bool:
        """Return True if the morphism type is injective or bijective.

        An injective bridge is one-to-one on its overlap region, meaning
        that distinct source coordinates map to distinct target coordinates.

        Returns
        -------
        bool
        """
        return self.morphism_type in ("injective", "bijective")

    def is_surjective(self) -> bool:
        """Return True if the morphism type is surjective or bijective.

        A surjective bridge covers every coordinate in the target overlap
        region, meaning no target coordinate is unreachable from source.

        Returns
        -------
        bool
        """
        return self.morphism_type in ("surjective", "bijective")

    def is_bijective(self) -> bool:
        """Return True if the morphism type is bijective.

        Returns
        -------
        bool
        """
        return self.morphism_type == "bijective"

    def get_overlap_size(self) -> int:
        """Return the number of coordinates in the overlap region.

        Returns
        -------
        int
        """
        return len(self.overlap_region)

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose_with(self, other: BridgeTheoremEncoding) -> BridgeTheoremEncoding:
        """Compose this bridge with *other*, producing a new bridge encoding.

        The composed bridge has:
        - ``source_pack_id`` equal to ``self.source_pack_id``
        - ``target_pack_id`` equal to ``other.target_pack_id``
        - ``overlap_region`` equal to the intersection of the two overlap regions
        - ``trust_ceiling`` equal to ``min(self.trust_ceiling, other.trust_ceiling)``
          (Lemma 35.3 — monotone trust)
        - ``source_formula`` equal to a chained formula string
        - ``morphism_type`` downgraded to ``"partial"`` unless both are bijective

        Parameters
        ----------
        other:
            The bridge to compose after this one.  Its ``source_pack_id`` need
            not equal ``self.target_pack_id`` (the composition is formal).

        Returns
        -------
        BridgeTheoremEncoding
            The composed bridge encoding.
        """
        composed_id = f"{self.bridge_id}>>>{other.bridge_id}"
        composed_overlap = self.overlap_region & other.overlap_region
        composed_trust = min(self.trust_ceiling, other.trust_ceiling)
        composed_formula_src = f"({self.source_formula}) ∘ ({other.source_formula})"
        composed_formula_tgt = f"({self.target_formula}) ∘ ({other.target_formula})"

        # Morphism type: bijective only if both are bijective; injective if both
        # injective; surjective if both surjective; otherwise partial.
        if self.morphism_type == "bijective" and other.morphism_type == "bijective":
            mtype = "bijective"
        elif self.is_injective() and other.is_injective():
            mtype = "injective"
        elif self.is_surjective() and other.is_surjective():
            mtype = "surjective"
        else:
            mtype = "partial"

        return BridgeTheoremEncoding(
            bridge_id=composed_id,
            source_pack_id=self.source_pack_id,
            target_pack_id=other.target_pack_id,
            overlap_region=frozenset(composed_overlap),
            source_formula=composed_formula_src,
            target_formula=composed_formula_tgt,
            trust_ceiling=composed_trust,
            morphism_type=mtype,
        )

    # ------------------------------------------------------------------
    # Evidence translation
    # ------------------------------------------------------------------

    def apply_to_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Translate an evidence dict through this bridge.

        Only keys in :attr:`overlap_region` are carried across.  The
        translated dict is augmented with a ``_provenance`` entry recording
        the bridge_id and formulas applied.

        Parameters
        ----------
        evidence:
            Evidence dict from the source pack.

        Returns
        -------
        dict
            Translated evidence dict for the target pack.
        """
        translated: dict[str, Any] = {}
        for key in self.overlap_region:
            if key in evidence:
                translated[key] = evidence[key]

        translated["_provenance"] = {
            "bridge_id": self.bridge_id,
            "source_pack_id": self.source_pack_id,
            "target_pack_id": self.target_pack_id,
            "source_formula": self.source_formula,
            "target_formula": self.target_formula,
            "trust_ceiling": self.trust_ceiling,
            "morphism_type": self.morphism_type,
        }
        return translated

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_morphism_laws(self) -> tuple[bool, list[str]]:
        """Check that this bridge satisfies the morphism axioms.

        Checks:
        - ``trust_ceiling`` is in [0.0, 1.0]
        - ``overlap_region`` is non-empty
        - ``source_formula`` is non-empty
        - ``target_formula`` is non-empty
        - ``source_pack_id != target_pack_id``
        - ``morphism_type`` is one of the allowed values

        Returns
        -------
        tuple[bool, list[str]]
            ``(True, [])`` if valid; ``(False, errors)`` otherwise.
        """
        errors: list[str] = []

        if not (0.0 <= self.trust_ceiling <= 1.0):
            errors.append(
                f"trust_ceiling {self.trust_ceiling} is out of [0, 1]"
            )
        if not self.overlap_region:
            errors.append("overlap_region is empty")
        if not self.source_formula:
            errors.append("source_formula is empty")
        if not self.target_formula:
            errors.append("target_formula is empty")
        if self.source_pack_id == self.target_pack_id:
            errors.append(
                f"source_pack_id and target_pack_id are identical: {self.source_pack_id!r}"
            )
        if self.morphism_type not in _VALID_MORPHISM_TYPES:
            errors.append(
                f"morphism_type {self.morphism_type!r} is not one of {sorted(_VALID_MORPHISM_TYPES)}"
            )

        return len(errors) == 0, errors

    # ------------------------------------------------------------------
    # Inversion
    # ------------------------------------------------------------------

    def invert(self) -> BridgeTheoremEncoding:
        """Return the inverse bridge (swapping source and target).

        Only valid for bijective bridges.  For other morphism types this
        operation is not defined in the theory.

        Returns
        -------
        BridgeTheoremEncoding
            The inverted bridge.

        Raises
        ------
        ValueError
            If :attr:`morphism_type` is not ``"bijective"``.
        """
        if self.morphism_type != "bijective":
            raise ValueError(
                f"Cannot invert a non-bijective bridge (morphism_type={self.morphism_type!r}). "
                "Only bijective bridges have well-defined inverses."
            )
        return BridgeTheoremEncoding(
            bridge_id=f"inv({self.bridge_id})",
            source_pack_id=self.target_pack_id,
            target_pack_id=self.source_pack_id,
            overlap_region=self.overlap_region,
            source_formula=self.target_formula,
            target_formula=self.source_formula,
            trust_ceiling=self.trust_ceiling,
            morphism_type="bijective",
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible plain dictionary.

        Returns
        -------
        dict
        """
        return {
            "bridge_id": self.bridge_id,
            "source_pack_id": self.source_pack_id,
            "target_pack_id": self.target_pack_id,
            "overlap_region": sorted(self.overlap_region),
            "source_formula": self.source_formula,
            "target_formula": self.target_formula,
            "trust_ceiling": self.trust_ceiling,
            "morphism_type": self.morphism_type,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BridgeTheoremEncoding:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        BridgeTheoremEncoding
        """
        return cls(
            bridge_id=d["bridge_id"],
            source_pack_id=d["source_pack_id"],
            target_pack_id=d["target_pack_id"],
            overlap_region=frozenset(d["overlap_region"]),
            source_formula=d["source_formula"],
            target_formula=d["target_formula"],
            trust_ceiling=float(d["trust_ceiling"]),
            morphism_type=d["morphism_type"],
        )

    def summary(self) -> str:
        """Return a human-readable one-block summary.

        Returns
        -------
        str
        """
        return (
            f"BridgeTheoremEncoding(id={self.bridge_id!r}, "
            f"{self.source_pack_id!r} -[{self.morphism_type}]-> {self.target_pack_id!r}, "
            f"overlap_size={self.get_overlap_size()}, "
            f"trust_ceiling={self.trust_ceiling:.3f})"
        )


# ---------------------------------------------------------------------------
# PackFederationEncoding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackFederationEncoding:
    """Frozen encoding of a complete pack federation as a sheaf.

    A pack federation encoding captures the global structure of a semantic
    federation: the set of participating pack IDs, the bridge encodings that
    specify how adjacent packs interact across their shared vocabulary, the
    reference to the federation protocol used during descent, and a summary
    status of whether the sheaf condition has been verified.

    Parameters
    ----------
    pack_ids:
        Frozen set of participating pack identifier strings.
    bridge_encodings:
        Tuple of :class:`BridgeTheoremEncoding` instances.
    federation_protocol_id:
        String identifier of the :class:`FederationProtocol` used.
    sheaf_condition_status:
        One of ``"satisfied"``, ``"violated"``, or ``"unknown"``.

    copilot: pack-federation-encoding
    """

    pack_ids: FrozenSet[str]
    bridge_encodings: tuple  # tuple[BridgeTheoremEncoding, ...]
    federation_protocol_id: str
    sheaf_condition_status: str

    # ------------------------------------------------------------------
    # Predicate
    # ------------------------------------------------------------------

    def is_valid_sheaf(self) -> bool:
        """Return True iff the sheaf condition is satisfied and all bridges are valid.

        A pack federation is a valid sheaf when:
        1. :attr:`sheaf_condition_status` is ``"satisfied"``
        2. Every :class:`BridgeTheoremEncoding` in :attr:`bridge_encodings`
           passes its own :meth:`~BridgeTheoremEncoding.validate_morphism_laws` check.

        Returns
        -------
        bool
        """
        if self.sheaf_condition_status != "satisfied":
            return False
        for bridge in self.bridge_encodings:
            ok, _ = bridge.validate_morphism_laws()
            if not ok:
                return False
        return True

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_pack_ids(self) -> list[str]:
        """Return sorted list of participating pack IDs.

        Returns
        -------
        list[str]
        """
        return sorted(self.pack_ids)

    def get_bridge_count(self) -> int:
        """Return the number of bridge encodings.

        Returns
        -------
        int
        """
        return len(self.bridge_encodings)

    def get_bridges_for_pack(self, pack_id: str) -> list[BridgeTheoremEncoding]:
        """Return all bridges where *pack_id* is source or target.

        Parameters
        ----------
        pack_id:
            Pack identifier to filter by.

        Returns
        -------
        list[BridgeTheoremEncoding]
        """
        return [
            b for b in self.bridge_encodings
            if b.source_pack_id == pack_id or b.target_pack_id == pack_id
        ]

    # ------------------------------------------------------------------
    # Overlap law validation
    # ------------------------------------------------------------------

    def validate_overlap_laws(self) -> tuple[bool, list[str]]:
        """Verify that every bridge satisfies overlap law requirements.

        For each bridge:
        - :attr:`~BridgeTheoremEncoding.overlap_region` must be non-empty.
        - :attr:`~BridgeTheoremEncoding.trust_ceiling` must be in [0, 1].
        - :attr:`~BridgeTheoremEncoding.source_pack_id` must be in :attr:`pack_ids`.
        - :attr:`~BridgeTheoremEncoding.target_pack_id` must be in :attr:`pack_ids`.

        Returns
        -------
        tuple[bool, list[str]]
            ``(True, [])`` if all laws hold; ``(False, errors)`` otherwise.
        """
        errors: list[str] = []
        for bridge in self.bridge_encodings:
            if not bridge.overlap_region:
                errors.append(
                    f"Bridge {bridge.bridge_id!r} has empty overlap_region"
                )
            if not (0.0 <= bridge.trust_ceiling <= 1.0):
                errors.append(
                    f"Bridge {bridge.bridge_id!r} trust_ceiling={bridge.trust_ceiling} out of [0,1]"
                )
            if bridge.source_pack_id not in self.pack_ids:
                errors.append(
                    f"Bridge {bridge.bridge_id!r} source_pack_id {bridge.source_pack_id!r} "
                    f"not in pack_ids"
                )
            if bridge.target_pack_id not in self.pack_ids:
                errors.append(
                    f"Bridge {bridge.bridge_id!r} target_pack_id {bridge.target_pack_id!r} "
                    f"not in pack_ids"
                )
        return len(errors) == 0, errors

    # ------------------------------------------------------------------
    # Cohomology
    # ------------------------------------------------------------------

    def compute_cohomology_class(self) -> str:
        """Compute a string label for the cohomology class of this federation.

        The computation follows theory2.tex §35 §35.4:
        - If the sheaf condition is satisfied and no bridge has trust < 0.5,
          the global section exists and we return ``"H^0(X, F)"`` (degree-0
          cohomology is non-trivial, meaning there are global sections).
        - If the sheaf condition is satisfied but some bridge has trust < 0.5,
          the global section is weakly trusted: ``"H^0(X, F)[weak]"``.
        - If the sheaf condition is violated (obstruction), return
          ``"H^1(X, F)[obstruction]"``.
        - Otherwise return ``"H^?(X, F)[unknown]"``.

        Returns
        -------
        str
        """
        if self.sheaf_condition_status == "satisfied":
            low_trust = any(b.trust_ceiling < 0.5 for b in self.bridge_encodings)
            if low_trust:
                return "H^0(X, F)[weak]"
            return "H^0(X, F)"
        elif self.sheaf_condition_status == "violated":
            return "H^1(X, F)[obstruction]"
        return "H^?(X, F)[unknown]"

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full federation encoding to a plain dictionary.

        Returns
        -------
        dict
        """
        return {
            "pack_ids": sorted(self.pack_ids),
            "bridge_encodings": [b.to_dict() for b in self.bridge_encodings],
            "federation_protocol_id": self.federation_protocol_id,
            "sheaf_condition_status": self.sheaf_condition_status,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PackFederationEncoding:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        PackFederationEncoding
        """
        bridges = tuple(
            BridgeTheoremEncoding.from_dict(bd) for bd in d.get("bridge_encodings", [])
        )
        return cls(
            pack_ids=frozenset(d["pack_ids"]),
            bridge_encodings=bridges,
            federation_protocol_id=d["federation_protocol_id"],
            sheaf_condition_status=d.get("sheaf_condition_status", "unknown"),
        )

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this federation encoding.

        Returns
        -------
        str
        """
        lines = [
            "PackFederationEncoding",
            f"  packs         : {', '.join(self.get_pack_ids())}",
            f"  bridges       : {self.get_bridge_count()}",
            f"  protocol_id   : {self.federation_protocol_id}",
            f"  sheaf_status  : {self.sheaf_condition_status}",
            f"  cohomology    : {self.compute_cohomology_class()}",
        ]
        for bridge in self.bridge_encodings:
            lines.append(f"    - {bridge.summary()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# FederationProtocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FederationProtocol:
    """Frozen encoding of a federation descent protocol.

    A federation protocol specifies an ordered sequence of bridge IDs that
    forms the descent path from one pack to another (or through multiple
    intermediate packs), together with a trust floor below which evidence is
    rejected and a kind-preservation mode that controls how semantic kind is
    treated during translation.

    Parameters
    ----------
    protocol_id:
        Unique identifier for this protocol.
    participating_packs:
        Frozen set of pack IDs involved in the protocol.
    bridge_sequence:
        Ordered tuple of bridge IDs to execute.
    trust_floor:
        Minimum acceptable trust level for evidence after descent.
    kind_preservation_mode:
        One of ``"strict"`` (kind must be unchanged), ``"relaxed"`` (kind
        drift is logged but allowed), or ``"advisory"`` (kind drift is ignored).
    descent_conditions:
        Tuple of strings describing preconditions for descent.

    copilot: federation-protocol-model
    """

    protocol_id: str
    participating_packs: FrozenSet[str]
    bridge_sequence: tuple  # tuple[str, ...]
    trust_floor: float
    kind_preservation_mode: str
    descent_conditions: tuple  # tuple[str, ...]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_descent(self) -> tuple[bool, list[str]]:
        """Validate descent protocol structural requirements.

        Checks:
        - :attr:`bridge_sequence` is non-empty.
        - :attr:`participating_packs` has at least 2 members.
        - :attr:`trust_floor` is in [0.0, 1.0].
        - :attr:`kind_preservation_mode` is one of the valid values.

        Returns
        -------
        tuple[bool, list[str]]
        """
        errors: list[str] = []
        if not self.bridge_sequence:
            errors.append("bridge_sequence is empty")
        if len(self.participating_packs) < 2:
            errors.append(
                f"participating_packs has {len(self.participating_packs)} member(s); need ≥2"
            )
        if not (0.0 <= self.trust_floor <= 1.0):
            errors.append(f"trust_floor {self.trust_floor} is out of [0, 1]")
        if self.kind_preservation_mode not in _VALID_KPM:
            errors.append(
                f"kind_preservation_mode {self.kind_preservation_mode!r} is not one of "
                f"{sorted(_VALID_KPM)}"
            )
        return len(errors) == 0, errors

    # ------------------------------------------------------------------
    # Execution simulation
    # ------------------------------------------------------------------

    def execute_step(self, step_index: int, state: dict[str, Any]) -> dict[str, Any]:
        """Simulate a single descent step, returning updated state.

        Copies *state*, adds a ``"step_{step_index}"`` key containing the
        bridge_id at that position in the sequence and a status string.
        If ``step_index`` is out of range, the status is ``"out_of_range"``.

        Parameters
        ----------
        step_index:
            Zero-based index into :attr:`bridge_sequence`.
        state:
            Current descent state dict.

        Returns
        -------
        dict
            New state dict with the step result added.
        """
        new_state = dict(state)
        if 0 <= step_index < len(self.bridge_sequence):
            bridge_id = self.bridge_sequence[step_index]
            new_state[f"step_{step_index}"] = {
                "bridge_id": bridge_id,
                "status": "executed",
                "step_index": step_index,
            }
        else:
            new_state[f"step_{step_index}"] = {
                "bridge_id": None,
                "status": "out_of_range",
                "step_index": step_index,
            }
        return new_state

    # ------------------------------------------------------------------
    # Trust computation
    # ------------------------------------------------------------------

    def compute_combined_trust(self, trusts: Sequence[float]) -> float:
        """Compute the combined trust across a sequence of bridge trusts.

        For ``"strict"`` mode returns ``min(trusts)``.
        For ``"relaxed"`` and ``"advisory"`` modes returns the harmonic mean,
        which is more generous than the minimum but still penalises any very
        low-trust bridge.

        Parameters
        ----------
        trusts:
            Sequence of trust values in [0, 1].

        Returns
        -------
        float
            Combined trust value, or ``1.0`` if *trusts* is empty.
        """
        if not trusts:
            return 1.0
        if self.kind_preservation_mode == "strict":
            return min(trusts)
        # harmonic mean
        if any(t == 0.0 for t in trusts):
            return 0.0
        return len(trusts) / sum(1.0 / t for t in trusts)

    # ------------------------------------------------------------------
    # Kind preservation check
    # ------------------------------------------------------------------

    def check_kind_preservation(self, evidence: dict[str, Any]) -> bool:
        """Check whether the semantic kind has been preserved during descent.

        Returns ``True`` if the ``"kind"`` key is present in *evidence* and
        its value matches ``evidence.get("original_kind")`` (or if
        ``"original_kind"`` is absent, indicating no reference kind was set).

        Parameters
        ----------
        evidence:
            Evidence dict after one or more descent steps.

        Returns
        -------
        bool
        """
        if "kind" not in evidence:
            return False
        original_kind = evidence.get("original_kind")
        if original_kind is None:
            return True
        return evidence["kind"] == original_kind

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_bridge_path(self) -> list[str]:
        """Return the ordered list of bridge IDs.

        Returns
        -------
        list[str]
        """
        return list(self.bridge_sequence)

    def is_executable(self) -> bool:
        """Return True if the protocol can be executed.

        A protocol is executable when :meth:`validate_descent` passes and
        :attr:`descent_conditions` is non-empty (conditions must be specified
        before execution).

        Returns
        -------
        bool
        """
        ok, _ = self.validate_descent()
        return ok and len(self.descent_conditions) > 0

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
        """
        return {
            "protocol_id": self.protocol_id,
            "participating_packs": sorted(self.participating_packs),
            "bridge_sequence": list(self.bridge_sequence),
            "trust_floor": self.trust_floor,
            "kind_preservation_mode": self.kind_preservation_mode,
            "descent_conditions": list(self.descent_conditions),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FederationProtocol:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        FederationProtocol
        """
        return cls(
            protocol_id=d["protocol_id"],
            participating_packs=frozenset(d["participating_packs"]),
            bridge_sequence=tuple(d["bridge_sequence"]),
            trust_floor=float(d["trust_floor"]),
            kind_preservation_mode=d["kind_preservation_mode"],
            descent_conditions=tuple(d.get("descent_conditions", [])),
        )

    def summary(self) -> str:
        """Return a human-readable summary string.

        Returns
        -------
        str
        """
        packs = ", ".join(sorted(self.participating_packs))
        path = " -> ".join(self.bridge_sequence) if self.bridge_sequence else "(empty)"
        return (
            f"FederationProtocol(id={self.protocol_id!r}, "
            f"packs=[{packs}], "
            f"path={path}, "
            f"trust_floor={self.trust_floor:.3f}, "
            f"kpm={self.kind_preservation_mode!r})"
        )


# ---------------------------------------------------------------------------
# PackBoundary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackBoundary:
    """Frozen encoding of a shared boundary between two adjacent packs.

    A pack boundary records the geometric interface between two packs: the
    set of coordinate names that are shared (:attr:`shared_coordinates`), the
    overlap laws (semantic consistency constraints) that must hold at the
    boundary, and a classification of the boundary type.

    Parameters
    ----------
    boundary_id:
        Unique identifier for this boundary.
    pack_a_id:
        Identifier of the first pack.
    pack_b_id:
        Identifier of the second pack.
    shared_coordinates:
        Frozen set of coordinate names present in both packs.
    overlap_laws:
        Tuple of law strings that must hold at the boundary.
    boundary_type:
        One of ``"interior"``, ``"frontier"``, ``"external"``.

    copilot: pack-boundary-model
    """

    boundary_id: str
    pack_a_id: str
    pack_b_id: str
    shared_coordinates: FrozenSet[str]
    overlap_laws: tuple  # tuple[str, ...]
    boundary_type: str

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_shared_vocabulary(self) -> list[str]:
        """Return sorted list of shared coordinate names.

        Returns
        -------
        list[str]
        """
        return sorted(self.shared_coordinates)

    def get_overlap_coordinates(self) -> list[str]:
        """Alias of :meth:`get_shared_vocabulary` for API uniformity.

        Returns
        -------
        list[str]
        """
        return self.get_shared_vocabulary()

    def is_empty(self) -> bool:
        """Return True if the shared coordinate set is empty.

        An empty boundary implies the two packs are disjoint — there is no
        overlap law to verify and the sheaf condition is vacuously satisfied
        for this boundary.

        Returns
        -------
        bool
        """
        return len(self.shared_coordinates) == 0

    def compute_overlap_measure(self) -> float:
        """Return a normalised overlap measure in [0, 1).

        Computed as ``|shared| / (|shared| + 1)`` — a monotone increasing
        function of the shared coordinate count that approaches 1 as the
        overlap grows but never reaches it (reflecting that no two packs
        can be identical under the disjointness axiom of §35.0).

        Returns
        -------
        float
        """
        n = len(self.shared_coordinates)
        return n / max(1, n + 1)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_boundary_laws(self) -> tuple[bool, list[str]]:
        """Check that this boundary satisfies its structural axioms.

        Checks:
        - :attr:`overlap_laws` is non-empty (boundaries must have laws).
        - :attr:`boundary_type` is one of the valid values.
        - :attr:`pack_a_id` != :attr:`pack_b_id`.

        Returns
        -------
        tuple[bool, list[str]]
        """
        errors: list[str] = []
        if not self.overlap_laws:
            errors.append("overlap_laws is empty; every boundary needs at least one law")
        if self.boundary_type not in _VALID_BOUNDARY_TYPES:
            errors.append(
                f"boundary_type {self.boundary_type!r} is not one of "
                f"{sorted(_VALID_BOUNDARY_TYPES)}"
            )
        if self.pack_a_id == self.pack_b_id:
            errors.append(
                f"pack_a_id and pack_b_id are identical: {self.pack_a_id!r}"
            )
        return len(errors) == 0, errors

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
        """
        return {
            "boundary_id": self.boundary_id,
            "pack_a_id": self.pack_a_id,
            "pack_b_id": self.pack_b_id,
            "shared_coordinates": sorted(self.shared_coordinates),
            "overlap_laws": list(self.overlap_laws),
            "boundary_type": self.boundary_type,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PackBoundary:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        PackBoundary
        """
        return cls(
            boundary_id=d["boundary_id"],
            pack_a_id=d["pack_a_id"],
            pack_b_id=d["pack_b_id"],
            shared_coordinates=frozenset(d["shared_coordinates"]),
            overlap_laws=tuple(d.get("overlap_laws", [])),
            boundary_type=d["boundary_type"],
        )

    def summarize(self) -> str:
        """Return a short human-readable summary string.

        Returns
        -------
        str
        """
        coord_count = len(self.shared_coordinates)
        law_count = len(self.overlap_laws)
        return (
            f"PackBoundary(id={self.boundary_id!r}, "
            f"{self.pack_a_id!r} ↔ {self.pack_b_id!r}, "
            f"shared={coord_count}, laws={law_count}, "
            f"type={self.boundary_type!r}, "
            f"measure={self.compute_overlap_measure():.3f})"
        )
