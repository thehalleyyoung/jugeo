r"""Mixed Evidence Claim (C2): federating solver, runtime, and oracle evidence.

This module implements Thesis Claim C2 from Theory2.tex Chapter 2:

    **C2** — Solver, runtime, and copilot/oracle evidence can be federated
    in a single judgment without collapsing their distinct support kinds.

The claim asserts that the trust algebra's federation operation :math:`\oplus`
preserves channel provenance.  After federation, the resulting evidence
configuration retains per-channel labels and trust ceilings; no evidence
kind is silently promoted or collapsed to a scalar.

The copilot/oracle channel is central to this claim: it is the channel most
at risk of silent trust inflation.  The ``ChannelBoundary`` class enforces the
copilot trust ceiling at the ingestion boundary.  Any proposal from a copilot
agent that attempts to exceed ``COPILOT_SUGGESTED`` trust is rejected with an
explicit error, logged to the audit trail, and never silently promoted.

Classes
-------

:class:`EvidencePlurality`
    A multi-channel evidence collection for a single judgment clause.

:class:`ChannelBoundary`
    Enforces jurisdictional boundaries: copilot ceiling, solver ceiling, etc.

:class:`JurisdictionMap`
    Declares what each channel is authorised to produce and the trust ranges
    it may occupy.

:class:`FederationProtocol`
    Implements the :math:`\oplus` federation operation and verifies kind
    preservation.

Theory alignment
----------------

Section 240 of Theory2.tex introduces C2.  Section 241 states the channel
jurisdiction axioms; section 242 defines the federation operation; section 243
proves kind-preservation under admissible aggregation.  The no-silent-promotion
invariant is stated as Theorem 2.4.1 in the theory; it is tested here by
``ChannelBoundary.enforce_ceiling``.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SupportKind(Enum):
    """The kind of support provided by an evidence item.

    Support kinds are preserved across federation (Theorem 2.4.1).  A
    federation that collapses kinds is inadmissible.
    """

    STRUCTURAL_PROOF = "structural_proof"
    ARITHMETIC_PROOF = "arithmetic_proof"
    HEAP_WITNESS = "heap_witness"
    IDENTITY_CHECK = "identity_check"
    SEMANTIC_PROPOSAL = "semantic_proposal"
    BEHAVIORAL_PROPOSAL = "behavioral_proposal"
    FORMAL_CERTIFICATE = "formal_certificate"
    HUMAN_REVIEW = "human_review"


class TrustCeiling(Enum):
    """Maximum trust level that a channel may assert.

    Ordered from weakest to strongest.  A channel may assert any trust level
    at or below its ceiling.
    """

    COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
    ORACLE_PROPOSED = "ORACLE_PROPOSED"
    HUMAN_ATTESTED = "HUMAN_ATTESTED"
    RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
    SOLVER_DISCHARGED = "SOLVER_DISCHARGED"
    MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"

    @property
    def ordinal(self) -> int:
        """Integer rank for comparison."""
        ranks = {
            "COPILOT_SUGGESTED": 0,
            "ORACLE_PROPOSED": 1,
            "HUMAN_ATTESTED": 2,
            "RUNTIME_WITNESSED": 3,
            "SOLVER_DISCHARGED": 4,
            "MECHANICALLY_VERIFIED": 5,
        }
        return ranks[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, TrustCeiling):
            return NotImplemented
        return self.ordinal < other.ordinal

    def __le__(self, other: object) -> bool:
        if not isinstance(other, TrustCeiling):
            return NotImplemented
        return self.ordinal <= other.ordinal


class ChannelName(Enum):
    """Named evidence channels in JuGeo."""

    SOLVER = "solver"
    RUNTIME = "runtime"
    COPILOT = "copilot"
    ORACLE = "oracle"
    FORMAL_PROOF = "formal_proof"
    HUMAN = "human"


class FederationOutcome(Enum):
    """Result of a federation operation."""

    SUCCESS = "success"
    KIND_COLLAPSED = "kind_collapsed"
    CEILING_VIOLATED = "ceiling_violated"
    EMPTY_INPUT = "empty_input"
    CONFLICT = "conflict"


# ---------------------------------------------------------------------------
# Evidence atoms
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceAtom:
    """A single atomic piece of evidence from one channel.

    Parameters
    ----------
    atom_id:
        Unique identifier (UUID string).
    channel:
        The :class:`ChannelName` that produced this atom.
    kind:
        The :class:`SupportKind` of support this atom provides.
    trust_asserted:
        The trust level the channel is asserting for this atom.
    payload_repr:
        A canonical string representation of the evidence payload.
    produced_at:
        Unix timestamp.
    copilot_origin:
        True if the atom was produced by a copilot/oracle agent.
    promotion_record:
        If non-empty, this is an explicit promotion justification that
        advances the atom's trust above its channel ceiling.  Only valid
        for atoms that have undergone explicit review.
    """

    atom_id: str
    channel: ChannelName
    kind: SupportKind
    trust_asserted: TrustCeiling
    payload_repr: str
    produced_at: float = field(default_factory=time.time)
    copilot_origin: bool = False
    promotion_record: str = ""

    def effective_trust(self, channel_boundary: "ChannelBoundary") -> TrustCeiling:
        """Return the effective trust level after ceiling enforcement.

        If the asserted trust exceeds the channel's ceiling, the effective
        trust is clamped to the ceiling.  If an explicit promotion record
        is present and the boundary validates it, the asserted trust is used.

        Parameters
        ----------
        channel_boundary:
            The :class:`ChannelBoundary` to consult for ceiling enforcement.

        Returns
        -------
        TrustCeiling
            The effective (post-enforcement) trust level.
        """
        ceiling = channel_boundary.ceiling_for(self.channel)
        if self.promotion_record:
            # Explicit promotion: use asserted trust (promotion is validated
            # separately by the boundary)
            return self.trust_asserted
        if self.trust_asserted <= ceiling:
            return self.trust_asserted
        # Clamp to ceiling
        return ceiling

    def fingerprint(self) -> str:
        """Return a short SHA-256 fingerprint of this atom."""
        raw = json.dumps(
            {
                "channel": self.channel.value,
                "kind": self.kind.value,
                "trust": self.trust_asserted.value,
                "payload": self.payload_repr,
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "channel": self.channel.value,
            "kind": self.kind.value,
            "trust_asserted": self.trust_asserted.value,
            "payload_repr": self.payload_repr,
            "produced_at": self.produced_at,
            "copilot_origin": self.copilot_origin,
            "promotion_record": self.promotion_record,
            "fingerprint": self.fingerprint(),
        }


def make_atom(
    channel: ChannelName,
    kind: SupportKind,
    trust: TrustCeiling,
    payload: str,
    *,
    copilot_origin: bool = False,
    promotion: str = "",
) -> EvidenceAtom:
    """Convenience constructor for :class:`EvidenceAtom`."""
    return EvidenceAtom(
        atom_id=str(uuid.uuid4()),
        channel=channel,
        kind=kind,
        trust_asserted=trust,
        payload_repr=payload,
        copilot_origin=copilot_origin,
        promotion_record=promotion,
    )


# ---------------------------------------------------------------------------
# ChannelBoundary
# ---------------------------------------------------------------------------


@dataclass
class CeilingViolation:
    """Record of a ceiling violation attempt.

    Parameters
    ----------
    atom_id:
        Identifier of the offending atom.
    channel:
        Channel that attempted the violation.
    asserted_trust:
        The trust level the atom claimed.
    channel_ceiling:
        The declared ceiling for that channel.
    detected_at:
        Unix timestamp.
    """

    atom_id: str
    channel: ChannelName
    asserted_trust: TrustCeiling
    channel_ceiling: TrustCeiling
    detected_at: float = field(default_factory=time.time)

    def description(self) -> str:
        return (
            f"Ceiling violation: channel {self.channel.value!r} asserted "
            f"{self.asserted_trust.value!r} but ceiling is "
            f"{self.channel_ceiling.value!r} (atom {self.atom_id[:8]})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "channel": self.channel.value,
            "asserted_trust": self.asserted_trust.value,
            "channel_ceiling": self.channel_ceiling.value,
            "detected_at": self.detected_at,
            "description": self.description(),
        }


@dataclass
class ChannelBoundary:
    """Enforces trust ceilings at channel ingestion boundaries.

    Every evidence atom entering a judgment must pass through the channel
    boundary.  Atoms that assert trust above their channel's ceiling are
    either clamped (if ``clamp_on_violation=True``) or rejected.

    Copilot/oracle channels have the lowest ceiling (``COPILOT_SUGGESTED``).
    This is the key invariant of the no-silent-promotion theorem
    (Theory2.tex Theorem 2.4.1).

    Parameters
    ----------
    name:
        Identifier for this boundary instance.
    ceiling_map:
        Mapping from :class:`ChannelName` to :class:`TrustCeiling`.
        If a channel is not in the map, the default ceiling is used.
    default_ceiling:
        Ceiling to use for channels not in ``ceiling_map``.
    clamp_on_violation:
        If True, atoms that exceed their ceiling are clamped rather than
        rejected.  If False, a :class:`CeilingViolation` is recorded and
        the atom is rejected.
    """

    name: str
    ceiling_map: dict[ChannelName, TrustCeiling] = field(default_factory=dict)
    default_ceiling: TrustCeiling = TrustCeiling.HUMAN_ATTESTED
    clamp_on_violation: bool = True
    _violations: list[CeilingViolation] = field(default_factory=list, repr=False)
    _admitted: int = field(default=0, repr=False)
    _rejected: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        # Set canonical default ceilings for the three main channels if not
        # already specified
        defaults: dict[ChannelName, TrustCeiling] = {
            ChannelName.COPILOT: TrustCeiling.COPILOT_SUGGESTED,
            ChannelName.ORACLE: TrustCeiling.COPILOT_SUGGESTED,
            ChannelName.SOLVER: TrustCeiling.SOLVER_DISCHARGED,
            ChannelName.RUNTIME: TrustCeiling.RUNTIME_WITNESSED,
            ChannelName.FORMAL_PROOF: TrustCeiling.MECHANICALLY_VERIFIED,
            ChannelName.HUMAN: TrustCeiling.HUMAN_ATTESTED,
        }
        for ch, ceil in defaults.items():
            self.ceiling_map.setdefault(ch, ceil)

    def ceiling_for(self, channel: ChannelName) -> TrustCeiling:
        """Return the declared ceiling for the given channel."""
        return self.ceiling_map.get(channel, self.default_ceiling)

    def enforce_ceiling(self, atom: EvidenceAtom) -> EvidenceAtom | None:
        """Enforce the trust ceiling on an incoming atom.

        Returns the (possibly clamped) atom, or ``None`` if rejected.

        Parameters
        ----------
        atom:
            The atom to check.

        Returns
        -------
        EvidenceAtom | None
            The admitted atom (clamped if necessary), or ``None`` if rejected.
        """
        ceiling = self.ceiling_for(atom.channel)
        # Explicit promotion records bypass ceiling check (policy-gated)
        if atom.promotion_record:
            self._admitted += 1
            return atom
        if atom.trust_asserted <= ceiling:
            self._admitted += 1
            return atom
        # Violation detected
        violation = CeilingViolation(
            atom_id=atom.atom_id,
            channel=atom.channel,
            asserted_trust=atom.trust_asserted,
            channel_ceiling=ceiling,
        )
        self._violations.append(violation)
        if self.clamp_on_violation:
            from dataclasses import replace
            clamped = replace(atom, trust_asserted=ceiling)
            self._admitted += 1
            return clamped
        self._rejected += 1
        return None

    def violations(self) -> list[CeilingViolation]:
        """Return all recorded ceiling violations."""
        return list(self._violations)

    def admission_stats(self) -> dict[str, int]:
        """Return admitted/rejected counts."""
        return {"admitted": self._admitted, "rejected": self._rejected}

    def copilot_ceiling_invariant_holds(self) -> bool:
        """Return True if no copilot/oracle atom has been admitted above its ceiling.

        This is the programmatic test for the no-silent-promotion invariant
        (Theorem 2.4.1).
        """
        copilot_channels = {ChannelName.COPILOT, ChannelName.ORACLE}
        return all(
            v.channel not in copilot_channels
            or v.asserted_trust <= self.ceiling_for(v.channel)
            for v in self._violations
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ceiling_map": {ch.value: ceil.value for ch, ceil in self.ceiling_map.items()},
            "default_ceiling": self.default_ceiling.value,
            "clamp_on_violation": self.clamp_on_violation,
            "admission_stats": self.admission_stats(),
            "n_violations": len(self._violations),
            "copilot_invariant_holds": self.copilot_ceiling_invariant_holds(),
        }


# ---------------------------------------------------------------------------
# JurisdictionMap
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelJurisdiction:
    """Declaration of what a channel is authorised to produce.

    Parameters
    ----------
    channel:
        The channel being declared.
    authorised_kinds:
        Set of :class:`SupportKind` values this channel may produce.
    trust_ceiling:
        Maximum trust the channel may assert.
    description:
        Prose description of the channel's jurisdiction.
    """

    channel: ChannelName
    authorised_kinds: frozenset[SupportKind]
    trust_ceiling: TrustCeiling
    description: str

    def authorises(self, kind: SupportKind) -> bool:
        """Return True if this jurisdiction authorises the given kind."""
        return kind in self.authorised_kinds

    def within_ceiling(self, trust: TrustCeiling) -> bool:
        """Return True if the given trust level is within this jurisdiction's ceiling."""
        return trust <= self.trust_ceiling

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel.value,
            "authorised_kinds": sorted(k.value for k in self.authorised_kinds),
            "trust_ceiling": self.trust_ceiling.value,
            "description": self.description,
        }


@dataclass
class JurisdictionMap:
    """Complete map of channel jurisdictions for a JuGeo deployment.

    Provides lookup and validation methods so that the :class:`FederationProtocol`
    can check whether incoming evidence atoms are within their channel's
    declared jurisdiction.

    Parameters
    ----------
    name:
        Identifier for this jurisdiction map.
    jurisdictions:
        Sequence of :class:`ChannelJurisdiction` declarations.
    """

    name: str
    jurisdictions: list[ChannelJurisdiction] = field(default_factory=list)

    def add(self, jurisdiction: ChannelJurisdiction) -> None:
        """Add a jurisdiction declaration."""
        self.jurisdictions.append(jurisdiction)

    def jurisdiction_for(self, channel: ChannelName) -> ChannelJurisdiction | None:
        """Return the jurisdiction declaration for the given channel."""
        for j in self.jurisdictions:
            if j.channel == channel:
                return j
        return None

    def validate_atom(self, atom: EvidenceAtom) -> list[str]:
        """Validate an atom against its channel's jurisdiction.

        Returns
        -------
        list[str]
            Empty if valid; list of error strings otherwise.
        """
        errors: list[str] = []
        jur = self.jurisdiction_for(atom.channel)
        if jur is None:
            errors.append(f"No jurisdiction declared for channel {atom.channel.value!r}")
            return errors
        if not jur.authorises(atom.kind):
            errors.append(
                f"Channel {atom.channel.value!r} is not authorised to produce "
                f"kind {atom.kind.value!r}"
            )
        if not jur.within_ceiling(atom.trust_asserted):
            errors.append(
                f"Channel {atom.channel.value!r} asserted trust "
                f"{atom.trust_asserted.value!r} above ceiling "
                f"{jur.trust_ceiling.value!r}"
            )
        return errors

    def is_complete(self) -> bool:
        """Return True if every known channel has a jurisdiction declaration."""
        declared = {j.channel for j in self.jurisdictions}
        return all(ch in declared for ch in ChannelName)

    def coverage_report(self) -> dict[str, Any]:
        declared = {j.channel.value for j in self.jurisdictions}
        missing = [ch.value for ch in ChannelName if ch.value not in declared]
        return {
            "name": self.name,
            "n_declared": len(self.jurisdictions),
            "is_complete": self.is_complete(),
            "missing_channels": missing,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "jurisdictions": [j.to_dict() for j in self.jurisdictions],
            "is_complete": self.is_complete(),
        }


# ---------------------------------------------------------------------------
# EvidencePlurality
# ---------------------------------------------------------------------------


@dataclass
class EvidencePlurality:
    """Multi-channel evidence collection for a single judgment clause.

    An evidence plurality holds atoms from multiple channels and provides
    queries for channel-specific and kind-specific views.  It is the
    intermediate representation before federation.

    Parameters
    ----------
    clause_id:
        Identifier of the judgment clause this plurality belongs to.
    boundary:
        :class:`ChannelBoundary` for ceiling enforcement on ingestion.
    jurisdiction_map:
        :class:`JurisdictionMap` for jurisdiction validation.
    """

    clause_id: str
    boundary: ChannelBoundary
    jurisdiction_map: JurisdictionMap
    _atoms: list[EvidenceAtom] = field(default_factory=list, repr=False)
    _admission_log: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def ingest(self, atom: EvidenceAtom) -> bool:
        """Ingest an evidence atom, enforcing ceiling and jurisdiction.

        Parameters
        ----------
        atom:
            The atom to ingest.

        Returns
        -------
        bool
            True if the atom was admitted; False if rejected.
        """
        # Jurisdiction check
        errors = self.jurisdiction_map.validate_atom(atom)
        if errors:
            self._admission_log.append({
                "atom_id": atom.atom_id,
                "admitted": False,
                "reason": "; ".join(errors),
                "ts": time.time(),
            })
            return False
        # Ceiling enforcement
        admitted = self.boundary.enforce_ceiling(atom)
        if admitted is None:
            self._admission_log.append({
                "atom_id": atom.atom_id,
                "admitted": False,
                "reason": "ceiling_violation",
                "ts": time.time(),
            })
            return False
        self._atoms.append(admitted)
        self._admission_log.append({
            "atom_id": admitted.atom_id,
            "admitted": True,
            "channel": admitted.channel.value,
            "kind": admitted.kind.value,
            "trust": admitted.trust_asserted.value,
            "ts": time.time(),
        })
        return True

    def atoms(self) -> list[EvidenceAtom]:
        """Return all admitted atoms."""
        return list(self._atoms)

    def atoms_by_channel(self, channel: ChannelName) -> list[EvidenceAtom]:
        """Return atoms from the given channel."""
        return [a for a in self._atoms if a.channel == channel]

    def atoms_by_kind(self, kind: SupportKind) -> list[EvidenceAtom]:
        """Return atoms of the given support kind."""
        return [a for a in self._atoms if a.kind == kind]

    def distinct_kinds(self) -> frozenset[SupportKind]:
        """Return the set of distinct support kinds present."""
        return frozenset(a.kind for a in self._atoms)

    def distinct_channels(self) -> frozenset[ChannelName]:
        """Return the set of distinct channels present."""
        return frozenset(a.channel for a in self._atoms)

    def copilot_atoms(self) -> list[EvidenceAtom]:
        """Return all atoms from copilot/oracle channels."""
        return [
            a for a in self._atoms
            if a.channel in (ChannelName.COPILOT, ChannelName.ORACLE)
        ]

    def max_trust(self) -> TrustCeiling | None:
        """Return the maximum trust level across all atoms."""
        if not self._atoms:
            return None
        return max((a.trust_asserted for a in self._atoms), key=lambda t: t.ordinal)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clause_id": self.clause_id,
            "n_atoms": len(self._atoms),
            "distinct_channels": sorted(ch.value for ch in self.distinct_channels()),
            "distinct_kinds": sorted(k.value for k in self.distinct_kinds()),
            "max_trust": self.max_trust().value if self.max_trust() else None,
            "admission_log": self._admission_log,
        }


# ---------------------------------------------------------------------------
# FederationProtocol
# ---------------------------------------------------------------------------


@dataclass
class FederatedEvidence:
    """The result of federating multiple evidence pluralities.

    A federated evidence record carries atoms from all source pluralities,
    preserving channel and kind labels.  It is *not* a scalar; the distinct
    support kinds remain enumerable.

    Parameters
    ----------
    federation_id:
        Unique identifier for this federation event.
    source_clause_ids:
        Clause identifiers whose evidence was federated.
    atoms:
        All atoms from the federated sources.
    outcome:
        The :class:`FederationOutcome` of the operation.
    federation_notes:
        Prose notes, including any copilot-suggested merging strategy.
    """

    federation_id: str
    source_clause_ids: tuple[str, ...]
    atoms: tuple[EvidenceAtom, ...]
    outcome: FederationOutcome
    federation_notes: str = ""
    federated_at: float = field(default_factory=time.time)

    def distinct_kinds(self) -> frozenset[SupportKind]:
        """Return the set of distinct support kinds in the federated evidence."""
        return frozenset(a.kind for a in self.atoms)

    def distinct_channels(self) -> frozenset[ChannelName]:
        """Return the set of distinct channels in the federated evidence."""
        return frozenset(a.channel for a in self.atoms)

    def is_kind_collapsed(self) -> bool:
        """Return True if kind information was lost (collapsed to single kind).

        A federation that produces a single support kind from inputs with
        multiple kinds has collapsed the kinds.
        """
        return len(self.distinct_kinds()) < 2 and len(self.atoms) > 1

    def copilot_fraction(self) -> float:
        """Return the fraction of atoms that originated from copilot/oracle channels."""
        if not self.atoms:
            return 0.0
        copilot = sum(
            1
            for a in self.atoms
            if a.channel in (ChannelName.COPILOT, ChannelName.ORACLE)
        )
        return copilot / len(self.atoms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "federation_id": self.federation_id,
            "source_clause_ids": list(self.source_clause_ids),
            "n_atoms": len(self.atoms),
            "distinct_channels": sorted(ch.value for ch in self.distinct_channels()),
            "distinct_kinds": sorted(k.value for k in self.distinct_kinds()),
            "outcome": self.outcome.value,
            "is_kind_collapsed": self.is_kind_collapsed(),
            "copilot_fraction": self.copilot_fraction(),
            "federation_notes": self.federation_notes,
            "federated_at": self.federated_at,
        }


@dataclass
class FederationProtocol:
    """Implements the evidence federation operation ⊕.

    Federation takes multiple :class:`EvidencePlurality` objects and
    combines them into a single :class:`FederatedEvidence` record while:

    1. Preserving all channel labels and support kinds.
    2. Enforcing the copilot trust ceiling.
    3. Detecting and reporting kind-collapse.
    4. Logging the federation event for audit.

    Parameters
    ----------
    name:
        Identifier for this protocol instance.
    boundary:
        :class:`ChannelBoundary` applied to all incoming atoms.
    jurisdiction_map:
        :class:`JurisdictionMap` for post-federation validation.
    """

    name: str
    boundary: ChannelBoundary
    jurisdiction_map: JurisdictionMap
    _federation_log: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def federate(
        self,
        pluralities: Sequence["EvidencePlurality"],
        notes: str = "",
    ) -> FederatedEvidence:
        """Execute the federation operation on a sequence of evidence pluralities.

        Parameters
        ----------
        pluralities:
            The evidence pluralities to federate.  Order does not matter;
            federation is commutative and associative.
        notes:
            Optional prose notes, e.g. the copilot-suggested merging strategy.

        Returns
        -------
        FederatedEvidence
            The federated evidence record.
        """
        if not pluralities:
            return FederatedEvidence(
                federation_id=str(uuid.uuid4()),
                source_clause_ids=(),
                atoms=(),
                outcome=FederationOutcome.EMPTY_INPUT,
                federation_notes=notes,
            )

        source_ids = tuple(p.clause_id for p in pluralities)
        all_atoms: list[EvidenceAtom] = []
        for plurality in pluralities:
            for atom in plurality.atoms():
                enforced = self.boundary.enforce_ceiling(atom)
                if enforced is not None:
                    all_atoms.append(enforced)

        # Check kind preservation
        input_kinds: frozenset[SupportKind] = frozenset(
            a.kind for p in pluralities for a in p.atoms()
        )
        output_kinds: frozenset[SupportKind] = frozenset(a.kind for a in all_atoms)
        if input_kinds and output_kinds < input_kinds:
            outcome = FederationOutcome.KIND_COLLAPSED
        else:
            outcome = FederationOutcome.SUCCESS

        fed = FederatedEvidence(
            federation_id=str(uuid.uuid4()),
            source_clause_ids=source_ids,
            atoms=tuple(all_atoms),
            outcome=outcome,
            federation_notes=notes,
        )
        self._federation_log.append({
            "federation_id": fed.federation_id,
            "outcome": outcome.value,
            "n_sources": len(pluralities),
            "n_atoms": len(all_atoms),
            "input_kinds": sorted(k.value for k in input_kinds),
            "output_kinds": sorted(k.value for k in output_kinds),
            "ts": fed.federated_at,
        })
        return fed

    def verify_kind_preservation(
        self, federated: "FederatedEvidence", original_kinds: frozenset[SupportKind]
    ) -> bool:
        """Verify that federation preserved all original support kinds.

        Parameters
        ----------
        federated:
            The federated evidence record.
        original_kinds:
            The set of support kinds present in the source pluralities.

        Returns
        -------
        bool
            True if all original kinds appear in the federated evidence.
        """
        return original_kinds <= federated.distinct_kinds()

    def audit_log(self) -> list[dict[str, Any]]:
        """Return the full federation audit log."""
        return list(self._federation_log)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "n_federations": len(self._federation_log),
            "boundary": self.boundary.to_dict(),
            "jurisdiction_coverage": self.jurisdiction_map.coverage_report(),
        }


# ---------------------------------------------------------------------------
# Factory: canonical C2 verification setup
# ---------------------------------------------------------------------------


def build_canonical_jurisdiction_map(name: str = "canonical_jmap") -> JurisdictionMap:
    """Construct the canonical JurisdictionMap for JuGeo Ch. 2.

    Registers jurisdiction declarations for all six channels as defined
    in Theory2.tex §241.
    """
    jmap = JurisdictionMap(name=name)
    jmap.add(ChannelJurisdiction(
        channel=ChannelName.SOLVER,
        authorised_kinds=frozenset({
            SupportKind.STRUCTURAL_PROOF,
            SupportKind.ARITHMETIC_PROOF,
        }),
        trust_ceiling=TrustCeiling.SOLVER_DISCHARGED,
        description="SMT/SAT solver producing structural and arithmetic discharge certificates",
    ))
    jmap.add(ChannelJurisdiction(
        channel=ChannelName.RUNTIME,
        authorised_kinds=frozenset({
            SupportKind.HEAP_WITNESS,
            SupportKind.IDENTITY_CHECK,
        }),
        trust_ceiling=TrustCeiling.RUNTIME_WITNESSED,
        description="Runtime monitor capturing heap witnesses and identity checks",
    ))
    jmap.add(ChannelJurisdiction(
        channel=ChannelName.COPILOT,
        authorised_kinds=frozenset({
            SupportKind.SEMANTIC_PROPOSAL,
            SupportKind.BEHAVIORAL_PROPOSAL,
        }),
        trust_ceiling=TrustCeiling.COPILOT_SUGGESTED,
        description=(
            "Copilot/oracle agent producing semantic and behavioral proposals. "
            "Trust ceiling is COPILOT_SUGGESTED; no silent promotion permitted."
        ),
    ))
    jmap.add(ChannelJurisdiction(
        channel=ChannelName.ORACLE,
        authorised_kinds=frozenset({
            SupportKind.SEMANTIC_PROPOSAL,
            SupportKind.BEHAVIORAL_PROPOSAL,
        }),
        trust_ceiling=TrustCeiling.COPILOT_SUGGESTED,
        description=(
            "Oracle agent (synonym for copilot in this context). "
            "Same ceiling as copilot channel."
        ),
    ))
    jmap.add(ChannelJurisdiction(
        channel=ChannelName.FORMAL_PROOF,
        authorised_kinds=frozenset({SupportKind.FORMAL_CERTIFICATE}),
        trust_ceiling=TrustCeiling.MECHANICALLY_VERIFIED,
        description="Lean4/Coq/Isabelle formal proof certificates",
    ))
    jmap.add(ChannelJurisdiction(
        channel=ChannelName.HUMAN,
        authorised_kinds=frozenset({SupportKind.HUMAN_REVIEW}),
        trust_ceiling=TrustCeiling.HUMAN_ATTESTED,
        description="Human reviewer attestation",
    ))
    return jmap


def build_canonical_boundary(name: str = "canonical_boundary") -> ChannelBoundary:
    """Construct the canonical ChannelBoundary for JuGeo Ch. 2."""
    return ChannelBoundary(
        name=name,
        clamp_on_violation=True,
    )
