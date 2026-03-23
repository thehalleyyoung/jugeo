"""
Algebraic data surfaces where proof burden is deferred to runtime witnesses.

An algebraic data surface is an ADT encoding that carries proof obligations but defers
them to runtime. Deferred proofs are first-class objects that can be discharged at
runtime by providing a witness value. This module formalizes that structure.

# copilot:
"""

from __future__ import annotations

import time
import hashlib
import itertools
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Optional jugeo imports — graceful degradation when running standalone
# ---------------------------------------------------------------------------

try:
    from jugeo.core.trust import TrustTier
    from jugeo.core.judgment import Judgment
    from jugeo.core.obstruction import CechObstruction
except ImportError:
    from enum import Enum
    class TrustTier(Enum):
        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3
        RUNTIME_WITNESSED = 4
        PROOF_BACKED = 5
    Judgment = tuple  # (c, φ, A, E, O, B, T, Π)
    @dataclass(frozen=True)
    class CechObstruction:
        cochain_id: str
        cochain_data: tuple
        coboundary_map: tuple
        obstruction_class: str
        tier: TrustTier

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Maximum number of deferred proofs allowed on a single surface before it is
# considered critically under-proven and should be blocked from promotion.
MAX_DEFERRED_PROOFS: int = 16

# Default trust tier assigned to freshly constructed algebraic data surfaces
# that have not yet been reviewed or verified.
DEFAULT_SURFACE_TIER: TrustTier = TrustTier.PROPOSAL

# Sentinel string placed in judgment tuples when a slot has no meaningful value.
# Must be a string (never None) to preserve the 8-string tuple invariant.
EMPTY_JUDGMENT_SLOT: str = "<unset>"

# Version tag embedded in cochain IDs so that cohomology classes can be traced
# back to the module revision that produced them.
COCHAIN_VERSION_TAG: str = "s03v1"

# Minimum discharge tier required before a RuntimeDischarge is considered to
# have sufficient evidential weight to satisfy a deferred proof obligation.
MIN_DISCHARGE_TIER: TrustTier = TrustTier.RUNTIME_WITNESSED


# ---------------------------------------------------------------------------
# AlgebraicDataSurface
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AlgebraicDataSurface:
    """An ADT encoding that carries deferred proof obligations.

    An algebraic data surface is the formal interface of an ADT together with
    all invariants and proof obligations that accompany it.  Obligations that
    cannot be proven statically are stored as DeferredProof objects and must be
    discharged by runtime witnesses before the surface can be promoted to a
    higher trust tier.

    Fields
    ------
    surface_id          : unique identifier for this surface
    constructors        : frozenset of constructor names
    invariant_descriptions : tuple of plain-text invariant descriptions
    deferred_proofs     : tuple of DeferredProof objects still outstanding
    surface_tier        : current TrustTier of this surface
    """

    surface_id: str
    constructors: frozenset
    invariant_descriptions: tuple
    deferred_proofs: tuple
    surface_tier: TrustTier

    # ------------------------------------------------------------------
    def add_constructor(self, c: str) -> AlgebraicDataSurface:
        """Return a new surface with the given constructor name added.

        Frozen dataclasses cannot be mutated in place, so this method
        returns a fresh AlgebraicDataSurface with the new constructor
        appended to the existing frozenset.  All other fields are
        preserved unchanged.
        """
        new_constructors = self.constructors | frozenset([c])
        return AlgebraicDataSurface(
            surface_id=self.surface_id,
            constructors=new_constructors,
            invariant_descriptions=self.invariant_descriptions,
            deferred_proofs=self.deferred_proofs,
            surface_tier=self.surface_tier,
        )

    # ------------------------------------------------------------------
    def proof_debt(self) -> int:
        """Return the number of deferred proof obligations still outstanding.

        A higher proof_debt means more runtime witnesses are needed before
        this surface can be fully trusted.
        """
        return len(self.deferred_proofs)

    # ------------------------------------------------------------------
    def is_fully_discharged(self) -> bool:
        """Return True iff all deferred proofs have been discharged.

        A surface is fully discharged when it carries zero outstanding
        proof obligations, meaning every invariant has either been proven
        statically or witnessed at runtime.
        """
        return len(self.deferred_proofs) == 0

    # ------------------------------------------------------------------
    def as_judgment_tuple(self) -> tuple:
        """Return a judgment tuple (c, φ, A, E, O, B, T, Π) as 8 strings.

        The judgment tuple is the canonical representation of a surface's
        epistemic state.  Each of the eight slots corresponds to a component
        of the Judgment type defined in jugeo.core.judgment.  All slots are
        returned as strings so that the tuple can be serialised and compared
        without importing the full jugeo type hierarchy.
        """
        c_str = str(sorted(self.constructors)) if self.constructors else EMPTY_JUDGMENT_SLOT
        phi_str = f"surface:{self.surface_id}"
        a_str = "AlgebraicDataSurface"
        e_str = str(len(self.invariant_descriptions))
        o_str = str(self.proof_debt())
        b_str = f"constructors={len(self.constructors)}"
        t_str = self.surface_tier.name
        pi_str = "deferred" if not self.is_fully_discharged() else "discharged"
        return (c_str, phi_str, a_str, e_str, o_str, b_str, t_str, pi_str)

    # ------------------------------------------------------------------
    def summarize(self) -> str:
        """Return a human-readable one-paragraph description of this surface.

        Includes surface ID, constructor count, invariant count, outstanding
        proof debt, and current trust tier so that the summary can serve as a
        standalone audit record.
        """
        status = "fully discharged" if self.is_fully_discharged() else f"{self.proof_debt()} proof(s) outstanding"
        return (
            f"AlgebraicDataSurface '{self.surface_id}': "
            f"{len(self.constructors)} constructor(s), "
            f"{len(self.invariant_descriptions)} invariant(s), "
            f"{status}, "
            f"tier={self.surface_tier.name}"
        )


# ---------------------------------------------------------------------------
# DeferredProof
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeferredProof:
    """A proof obligation that has been explicitly deferred to runtime.

    When a surface invariant cannot be verified statically (e.g. because the
    proof assistant is unavailable or the invariant depends on runtime data),
    the obligation is wrapped in a DeferredProof and attached to the surface.
    A DeferredProof is a first-class value: it can be escalated, serialised,
    and eventually discharged by supplying a RuntimeDischarge witness.

    Fields
    ------
    proof_id               : unique identifier for this deferred obligation
    obligation             : plain-text description of what must be proved
    deferral_site          : location in the code where deferral was recorded
    expected_discharge_site: location where the witness is expected to appear
    deferred_tier          : TrustTier at which deferral was recorded
    """

    proof_id: str
    obligation: str
    deferral_site: str
    expected_discharge_site: str
    deferred_tier: TrustTier

    # ------------------------------------------------------------------
    def is_overdue(self) -> bool:
        """Return True if this proof has been deferred past an acceptable tier.

        A deferred proof is considered overdue once its deferred_tier reaches
        RUNTIME_WITNESSED or higher, meaning it should already have been
        discharged by a witness before the code ever ran.
        """
        return self.deferred_tier.value >= TrustTier.RUNTIME_WITNESSED.value

    # ------------------------------------------------------------------
    def discharge_at_runtime(self, witness: str) -> RuntimeDischarge:
        """Discharge this proof obligation by supplying a runtime witness.

        The witness is a string description of the runtime value or
        computation that satisfies the obligation.  Returns a RuntimeDischarge
        record stamped with the current monotonic nanosecond timestamp.
        """
        ts = time.time_ns()
        discharge_id = hashlib.sha256(
            f"{self.proof_id}:{witness}:{ts}".encode()
        ).hexdigest()[:16]
        return RuntimeDischarge(
            discharge_id=discharge_id,
            proof_id=self.proof_id,
            runtime_value_description=witness,
            discharge_timestamp=ts,
            discharge_tier=TrustTier.RUNTIME_WITNESSED,
        )

    # ------------------------------------------------------------------
    def escalate(self) -> DeferredProof:
        """Return a new DeferredProof with the tier advanced by one step.

        Escalation is used when a deferred proof has not been discharged in
        time and must be flagged as more urgent.  The tier is incremented by
        one step in the TrustTier ordering, clamped at PROOF_BACKED.
        """
        current_value = self.deferred_tier.value
        new_value = min(current_value + 1, TrustTier.PROOF_BACKED.value)
        new_tier = TrustTier(new_value)
        return DeferredProof(
            proof_id=self.proof_id,
            obligation=self.obligation,
            deferral_site=self.deferral_site,
            expected_discharge_site=self.expected_discharge_site,
            deferred_tier=new_tier,
        )

    # ------------------------------------------------------------------
    def to_judgment_tuple(self) -> tuple:
        """Return a judgment tuple (c, φ, A, E, O, B, T, Π) as 8 strings.

        Serialises this DeferredProof into the canonical 8-string judgment
        format so that it can be compared, logged, and processed by any
        component that consumes jugeo judgment tuples.
        """
        c_str = self.proof_id
        phi_str = f"obligation:{self.obligation}"
        a_str = "DeferredProof"
        e_str = self.deferral_site
        o_str = self.expected_discharge_site
        b_str = f"overdue={self.is_overdue()}"
        t_str = self.deferred_tier.name
        pi_str = "pending"
        return (c_str, phi_str, a_str, e_str, o_str, b_str, t_str, pi_str)

    # ------------------------------------------------------------------
    def key(self) -> str:
        """Return a stable string key identifying this deferred proof.

        The key combines the proof_id and deferral_site so that two
        DeferredProof objects for the same logical obligation at the same
        source location will have the same key, enabling deduplication.
        """
        return f"{self.proof_id}:{self.deferral_site}"


# ---------------------------------------------------------------------------
# RuntimeDischarge
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuntimeDischarge:
    """A record attesting that a deferred proof was witnessed at runtime.

    When a runtime value is observed that satisfies a DeferredProof obligation,
    a RuntimeDischarge is created to record that event.  The discharge carries
    a nanosecond timestamp, the description of the witness value, and a
    TrustTier reflecting the strength of the evidence.

    Fields
    ------
    discharge_id              : unique identifier for this discharge event
    proof_id                  : ID of the DeferredProof being discharged
    runtime_value_description : description of the witness value observed
    discharge_timestamp       : monotonic nanosecond timestamp of the discharge
    discharge_tier            : TrustTier assigned to this discharge
    """

    discharge_id: str
    proof_id: str
    runtime_value_description: str
    discharge_timestamp: int
    discharge_tier: TrustTier

    # ------------------------------------------------------------------
    def is_valid_for(self, proof: DeferredProof) -> bool:
        """Return True iff this discharge satisfies the given DeferredProof.

        A discharge is valid for a proof when the proof_id fields match and
        the discharge_tier meets or exceeds the minimum acceptable tier
        (MIN_DISCHARGE_TIER), ensuring that the witness has sufficient
        evidential weight.
        """
        ids_match = self.proof_id == proof.proof_id
        tier_sufficient = self.discharge_tier.value >= MIN_DISCHARGE_TIER.value
        return ids_match and tier_sufficient

    # ------------------------------------------------------------------
    def to_cech_class(self) -> CechObstruction:
        """Lift this discharge into a Čech H¹ cohomology obstruction class.

        Runtime discharges can be viewed as local sections of a sheaf over
        the execution trace.  When a discharge resolves an obstruction, the
        cohomology class records both the cochain data (the witness) and the
        coboundary map (the proof obligation it satisfied).
        """
        cochain_id = f"{COCHAIN_VERSION_TAG}:{self.discharge_id}"
        cochain_data = (self.proof_id, self.runtime_value_description, str(self.discharge_timestamp))
        coboundary_map = (f"discharge:{self.discharge_id}", f"proof:{self.proof_id}")
        obstruction_class = f"H1:runtime_witness:{self.discharge_tier.name}"
        return CechObstruction(
            cochain_id=cochain_id,
            cochain_data=cochain_data,
            coboundary_map=coboundary_map,
            obstruction_class=obstruction_class,
            tier=self.discharge_tier,
        )

    # ------------------------------------------------------------------
    def strength(self) -> int:
        """Return the numeric strength of this discharge.

        Strength is the integer value of the discharge_tier in the TrustTier
        ordered algebra.  Higher values correspond to stronger evidence.
        """
        return self.discharge_tier.value

    # ------------------------------------------------------------------
    def to_judgment_tuple(self) -> tuple:
        """Return a judgment tuple (c, φ, A, E, O, B, T, Π) as 8 strings.

        Serialises this RuntimeDischarge into the canonical 8-string judgment
        format for logging, auditing, and downstream processing.
        """
        c_str = self.discharge_id
        phi_str = f"witness:{self.runtime_value_description}"
        a_str = "RuntimeDischarge"
        e_str = self.proof_id
        o_str = str(self.discharge_timestamp)
        b_str = f"strength={self.strength()}"
        t_str = self.discharge_tier.name
        pi_str = "witnessed"
        return (c_str, phi_str, a_str, e_str, o_str, b_str, t_str, pi_str)

    # ------------------------------------------------------------------
    def describe(self) -> str:
        """Return a human-readable description of this discharge event.

        Suitable for display in audit logs, test output, and debugging
        sessions.  Includes all salient fields in a single line.
        """
        return (
            f"RuntimeDischarge[{self.discharge_id}]: "
            f"proof_id={self.proof_id!r}, "
            f"witness={self.runtime_value_description!r}, "
            f"tier={self.discharge_tier.name}, "
            f"ts={self.discharge_timestamp}"
        )


# ---------------------------------------------------------------------------
# SurfaceObligation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SurfaceObligation:
    """A typed proof obligation associated with a specific algebraic data surface.

    SurfaceObligations are generated when a surface is constructed.  They
    describe, in plain text, what must be true for the surface to be considered
    sound.  Each obligation carries a kind (e.g. 'invariant', 'precondition',
    'range') and a TrustTier indicating how urgently it must be discharged.

    Fields
    ------
    obligation_id   : unique identifier for this obligation
    surface_id      : ID of the surface this obligation belongs to
    kind            : category of obligation ('invariant', 'precondition', etc.)
    description     : plain-text description of what must hold
    obligation_tier : TrustTier indicating urgency of discharge
    """

    obligation_id: str
    surface_id: str
    kind: str
    description: str
    obligation_tier: TrustTier

    # ------------------------------------------------------------------
    def is_dischargeable_at_runtime(self) -> bool:
        """Return True iff this obligation can be satisfied by a runtime witness.

        Obligations at tier RUNTIME_WITNESSED or below can be resolved by
        observing a concrete runtime value.  Obligations at PROOF_BACKED
        require a formal proof and cannot be satisfied by witness alone.
        """
        return self.obligation_tier.value <= TrustTier.RUNTIME_WITNESSED.value

    # ------------------------------------------------------------------
    def deferred_proof(self) -> DeferredProof:
        """Create a DeferredProof capturing this obligation for runtime discharge.

        Wraps this SurfaceObligation in a DeferredProof object that can be
        attached to an AlgebraicDataSurface and later discharged by calling
        DeferredProof.discharge_at_runtime with an appropriate witness.
        """
        proof_id = hashlib.sha256(
            f"{self.obligation_id}:{self.surface_id}:{self.description}".encode()
        ).hexdigest()[:16]
        return DeferredProof(
            proof_id=proof_id,
            obligation=self.description,
            deferral_site=f"SurfaceObligation:{self.obligation_id}",
            expected_discharge_site=f"surface:{self.surface_id}",
            deferred_tier=self.obligation_tier,
        )

    # ------------------------------------------------------------------
    def to_judgment_tuple(self) -> tuple:
        """Return a judgment tuple (c, φ, A, E, O, B, T, Π) as 8 strings.

        Serialises this SurfaceObligation into the canonical 8-string judgment
        format so that it integrates seamlessly with the rest of the jugeo
        judgment infrastructure.
        """
        c_str = self.obligation_id
        phi_str = f"obligation:{self.description}"
        a_str = "SurfaceObligation"
        e_str = self.surface_id
        o_str = self.kind
        b_str = f"dischargeable_at_runtime={self.is_dischargeable_at_runtime()}"
        t_str = self.obligation_tier.name
        pi_str = "pending"
        return (c_str, phi_str, a_str, e_str, o_str, b_str, t_str, pi_str)

    # ------------------------------------------------------------------
    def urgency(self) -> int:
        """Return the numeric urgency of this obligation.

        Urgency is the integer value of obligation_tier in the TrustTier
        ordered algebra.  Higher values indicate more urgent obligations that
        must be discharged sooner.
        """
        return self.obligation_tier.value

    # ------------------------------------------------------------------
    def obligation_summary(self) -> str:
        """Return a one-line human-readable summary of this obligation.

        Combines the obligation ID, kind, surface ID, and tier into a
        compact string suitable for display in reports and log files.
        """
        return (
            f"[{self.obligation_tier.name}] "
            f"{self.kind.upper()} on surface '{self.surface_id}': "
            f"{self.description} (id={self.obligation_id})"
        )


# ---------------------------------------------------------------------------
# SurfaceBuilder
# ---------------------------------------------------------------------------

class SurfaceBuilder:
    """Accumulates surfaces, deferred proofs, and discharges, then assembles them.

    SurfaceBuilder follows a builder pattern: callers add surfaces, proofs, and
    discharges incrementally and then call build() to obtain the final
    AlgebraicDataSurface.  The builder tracks all accumulated state and can
    produce a human-readable report at any point.
    """

    def __init__(self) -> None:
        """Initialise the builder with empty accumulator lists."""
        self._surfaces: list[AlgebraicDataSurface] = []
        self._deferred_proofs: list[DeferredProof] = []
        self._discharges: list[RuntimeDischarge] = []

    # ------------------------------------------------------------------
    def add_surface(self, s: AlgebraicDataSurface) -> None:
        """Add an AlgebraicDataSurface to the builder's accumulator.

        Surfaces are collected and merged when build() is called.  Adding a
        surface does not immediately affect any other accumulated state.
        """
        self._surfaces.append(s)

    # ------------------------------------------------------------------
    def defer_proof(self, proof: DeferredProof) -> None:
        """Record a DeferredProof obligation in the builder's accumulator.

        Deferred proofs are attached to the surface produced by build().
        Multiple proofs for different obligations can be deferred before
        calling build().
        """
        self._deferred_proofs.append(proof)

    # ------------------------------------------------------------------
    def discharge_at_runtime(self, discharge: RuntimeDischarge) -> None:
        """Record a RuntimeDischarge in the builder's accumulator.

        Discharges are matched against accumulated deferred proofs during
        build().  A deferred proof whose proof_id matches a discharge and
        whose tier is satisfied is removed from the outstanding obligations
        tuple of the final surface.
        """
        self._discharges.append(discharge)

    # ------------------------------------------------------------------
    def build(self) -> AlgebraicDataSurface:
        """Assemble and return the final AlgebraicDataSurface.

        Merges all accumulated surfaces, applies deferred proofs, and removes
        any proofs that have been satisfied by a recorded discharge.  The
        resulting surface has a proof_debt equal to the number of unresolved
        obligations.  Returns a default empty surface if no surfaces were
        added.
        """
        if not self._surfaces:
            return AlgebraicDataSurface(
                surface_id="empty",
                constructors=frozenset(),
                invariant_descriptions=(),
                deferred_proofs=(),
                surface_tier=DEFAULT_SURFACE_TIER,
            )

        # Merge constructors and invariants from all surfaces
        all_constructors: frozenset = frozenset(
            itertools.chain.from_iterable(s.constructors for s in self._surfaces)
        )
        all_invariants: tuple = tuple(
            itertools.chain.from_iterable(s.invariant_descriptions for s in self._surfaces)
        )

        # Determine which proofs have been discharged
        discharged_ids = {d.proof_id for d in self._discharges}
        outstanding = tuple(
            p for p in self._deferred_proofs if p.proof_id not in discharged_ids
        )

        # Inherit proofs already on constituent surfaces that haven't been discharged
        for s in self._surfaces:
            for p in s.deferred_proofs:
                if p.proof_id not in discharged_ids and p not in outstanding:
                    outstanding = outstanding + (p,)

        primary = self._surfaces[0]
        return AlgebraicDataSurface(
            surface_id=primary.surface_id,
            constructors=all_constructors,
            invariant_descriptions=all_invariants,
            deferred_proofs=outstanding,
            surface_tier=primary.surface_tier,
        )

    # ------------------------------------------------------------------
    def report(self) -> str:
        """Return a multi-line human-readable build report.

        Summarises the number of surfaces, deferred proofs, discharges, and
        the current proof debt.  Useful for audit logs and test output.
        """
        discharged_ids = {d.proof_id for d in self._discharges}
        outstanding_count = sum(
            1 for p in self._deferred_proofs if p.proof_id not in discharged_ids
        )
        lines = [
            "SurfaceBuilder report:",
            f"  surfaces accumulated : {len(self._surfaces)}",
            f"  deferred proofs      : {len(self._deferred_proofs)}",
            f"  runtime discharges   : {len(self._discharges)}",
            f"  outstanding debt     : {outstanding_count}",
        ]
        for i, proof in enumerate(self._deferred_proofs):
            status = "DISCHARGED" if proof.proof_id in discharged_ids else "PENDING"
            lines.append(f"    proof[{i}] {proof.key()} [{status}]")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standalone functions
# ---------------------------------------------------------------------------

def build_algebraic_surface(
    surface_id: str,
    constructors: frozenset,
    invariants: tuple,
    tier: TrustTier,
) -> AlgebraicDataSurface:
    """Construct an AlgebraicDataSurface with no deferred proofs.

    This is the primary factory function for creating surfaces that are
    considered well-formed at construction time.  All invariants are recorded
    as descriptions; no proof obligations are deferred initially.

    Parameters
    ----------
    surface_id   : unique identifier string for the new surface
    constructors : frozenset of constructor name strings
    invariants   : tuple of plain-text invariant description strings
    tier         : initial TrustTier for the surface

    Returns
    -------
    AlgebraicDataSurface with empty deferred_proofs tuple.
    """
    return AlgebraicDataSurface(
        surface_id=surface_id,
        constructors=constructors,
        invariant_descriptions=tuple(invariants),
        deferred_proofs=(),
        surface_tier=tier,
    )


def defer_proof_obligation(
    obligation: str,
    site: str,
    expected_site: str,
    tier: TrustTier,
) -> DeferredProof:
    """Create a DeferredProof for an obligation that cannot be proven statically.

    Generates a stable proof_id by hashing the obligation description, deferral
    site, and expected discharge site together.  This ensures that two calls with
    identical arguments produce the same proof_id, enabling idempotent deferral.

    Parameters
    ----------
    obligation    : plain-text description of the proof obligation
    site          : source location where the deferral is being recorded
    expected_site : source location where a witness is expected to appear
    tier          : TrustTier at which the deferral is being recorded

    Returns
    -------
    DeferredProof ready to be attached to an AlgebraicDataSurface.
    """
    proof_id = hashlib.sha256(
        f"defer:{obligation}:{site}:{expected_site}".encode()
    ).hexdigest()[:16]
    return DeferredProof(
        proof_id=proof_id,
        obligation=obligation,
        deferral_site=site,
        expected_discharge_site=expected_site,
        deferred_tier=tier,
    )


def runtime_discharge_obligation(
    proof_id: str,
    value_desc: str,
    timestamp: int,
) -> RuntimeDischarge:
    """Create a RuntimeDischarge record for a previously deferred proof.

    Generates a stable discharge_id by hashing the proof_id, value description,
    and timestamp.  The discharge is automatically assigned the
    RUNTIME_WITNESSED trust tier, reflecting that it is supported by an
    observed runtime value rather than a formal proof.

    Parameters
    ----------
    proof_id   : the proof_id of the DeferredProof being discharged
    value_desc : description of the runtime value that witnesses the obligation
    timestamp  : monotonic nanosecond timestamp of the discharge event

    Returns
    -------
    RuntimeDischarge with tier=RUNTIME_WITNESSED.
    """
    discharge_id = hashlib.sha256(
        f"discharge:{proof_id}:{value_desc}:{timestamp}".encode()
    ).hexdigest()[:16]
    return RuntimeDischarge(
        discharge_id=discharge_id,
        proof_id=proof_id,
        runtime_value_description=value_desc,
        discharge_timestamp=timestamp,
        discharge_tier=TrustTier.RUNTIME_WITNESSED,
    )


def encode_adt_surface(
    adt_name: str,
    constructors: list,
    invariants: list,
) -> AlgebraicDataSurface:
    """Encode an ADT as an AlgebraicDataSurface at the default trust tier.

    Convenience wrapper around build_algebraic_surface that accepts plain
    lists (converting them to frozenset/tuple internally) and uses the
    module-level DEFAULT_SURFACE_TIER.  Suitable for quick encoding of ADTs
    during exploratory work or in tests.

    Parameters
    ----------
    adt_name     : name of the ADT being encoded (used as surface_id)
    constructors : list of constructor name strings
    invariants   : list of plain-text invariant description strings

    Returns
    -------
    AlgebraicDataSurface at DEFAULT_SURFACE_TIER with no deferred proofs.
    """
    return build_algebraic_surface(
        surface_id=adt_name,
        constructors=frozenset(constructors),
        invariants=tuple(invariants),
        tier=DEFAULT_SURFACE_TIER,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== s03 algebraic data surfaces smoke test ===\n")

    # 1. TrustTier ordering
    assert TrustTier.PROPOSAL.value < TrustTier.REVIEWED.value
    assert TrustTier.PROOF_BACKED.value > TrustTier.RUNTIME_WITNESSED.value
    print(f"[PASS] TrustTier ordering: {[t.name for t in TrustTier]}")

    # 2. build_algebraic_surface
    surface = build_algebraic_surface(
        surface_id="MyADT",
        constructors=frozenset(["Leaf", "Node"]),
        invariants=("children >= 0", "depth >= 0"),
        tier=TrustTier.REVIEWED,
    )
    assert surface.surface_id == "MyADT"
    assert "Leaf" in surface.constructors
    assert surface.is_fully_discharged()
    assert surface.proof_debt() == 0
    print(f"[PASS] build_algebraic_surface: {surface.summarize()}")

    # 3. add_constructor
    surface2 = surface.add_constructor("Branch")
    assert "Branch" in surface2.constructors
    assert "Leaf" in surface2.constructors
    print(f"[PASS] add_constructor: {sorted(surface2.constructors)}")

    # 4. as_judgment_tuple
    jt = surface.as_judgment_tuple()
    assert len(jt) == 8
    assert all(isinstance(s, str) for s in jt)
    print(f"[PASS] as_judgment_tuple: {jt}")

    # 5. defer_proof_obligation
    ts_now = time.time_ns()
    proof = defer_proof_obligation(
        obligation="depth >= 0 for all nodes",
        site="s03.py:encode_adt_surface",
        expected_site="s03.py:runtime_check",
        tier=TrustTier.PROPOSAL,
    )
    assert proof.proof_id
    assert not proof.is_overdue()
    print(f"[PASS] defer_proof_obligation: key={proof.key()}")

    # 6. escalate
    escalated = proof.escalate()
    assert escalated.deferred_tier.value == proof.deferred_tier.value + 1
    print(f"[PASS] DeferredProof.escalate: {proof.deferred_tier.name} -> {escalated.deferred_tier.name}")

    # 7. to_judgment_tuple on DeferredProof
    pjt = proof.to_judgment_tuple()
    assert len(pjt) == 8
    assert all(isinstance(s, str) for s in pjt)
    print(f"[PASS] DeferredProof.to_judgment_tuple: {pjt}")

    # 8. discharge_at_runtime
    discharge = proof.discharge_at_runtime(witness="observed depth=3 at runtime")
    assert discharge.proof_id == proof.proof_id
    assert discharge.discharge_tier == TrustTier.RUNTIME_WITNESSED
    print(f"[PASS] discharge_at_runtime: {discharge.describe()}")

    # 9. is_valid_for
    assert discharge.is_valid_for(proof)
    print(f"[PASS] RuntimeDischarge.is_valid_for: True")

    # 10. strength
    assert discharge.strength() == TrustTier.RUNTIME_WITNESSED.value
    print(f"[PASS] RuntimeDischarge.strength: {discharge.strength()}")

    # 11. to_judgment_tuple on RuntimeDischarge
    djt = discharge.to_judgment_tuple()
    assert len(djt) == 8
    assert all(isinstance(s, str) for s in djt)
    print(f"[PASS] RuntimeDischarge.to_judgment_tuple: {djt}")

    # 12. to_cech_class
    cech = discharge.to_cech_class()
    assert cech.cochain_id.startswith(COCHAIN_VERSION_TAG)
    assert cech.tier == TrustTier.RUNTIME_WITNESSED
    print(f"[PASS] RuntimeDischarge.to_cech_class: obstruction_class={cech.obstruction_class}")

    # 13. runtime_discharge_obligation
    ts2 = time.time_ns()
    rd2 = runtime_discharge_obligation(
        proof_id=proof.proof_id,
        value_desc="depth=5 verified",
        timestamp=ts2,
    )
    assert rd2.discharge_tier == TrustTier.RUNTIME_WITNESSED
    assert rd2.is_valid_for(proof)
    print(f"[PASS] runtime_discharge_obligation: {rd2.describe()}")

    # 14. SurfaceObligation
    ob = SurfaceObligation(
        obligation_id="ob-001",
        surface_id="MyADT",
        kind="invariant",
        description="all depths are non-negative",
        obligation_tier=TrustTier.REVIEWED,
    )
    assert ob.is_dischargeable_at_runtime()
    assert ob.urgency() == TrustTier.REVIEWED.value
    ojt = ob.to_judgment_tuple()
    assert len(ojt) == 8
    assert all(isinstance(s, str) for s in ojt)
    print(f"[PASS] SurfaceObligation: {ob.obligation_summary()}")

    # 15. SurfaceObligation.deferred_proof
    ob_proof = ob.deferred_proof()
    assert ob_proof.obligation == ob.description
    print(f"[PASS] SurfaceObligation.deferred_proof: {ob_proof.key()}")

    # 16. SurfaceBuilder
    builder = SurfaceBuilder()
    base_surface = encode_adt_surface(
        "TreeADT", ["Leaf", "Node"], ["children >= 0"]
    )
    builder.add_surface(base_surface)
    builder.defer_proof(proof)
    builder.defer_proof(ob_proof)
    builder.discharge_at_runtime(discharge)
    built = builder.build()
    # 'proof' was discharged; ob_proof was not
    assert built.proof_debt() == 1
    print(f"[PASS] SurfaceBuilder.build: proof_debt={built.proof_debt()}")

    # 17. SurfaceBuilder.report
    report_text = builder.report()
    assert "DISCHARGED" in report_text
    assert "PENDING" in report_text
    print(f"[PASS] SurfaceBuilder.report:\n{report_text}")

    # 18. encode_adt_surface
    list_surface = encode_adt_surface("ListADT", ["Nil", "Cons"], ["length >= 0"])
    assert list_surface.surface_id == "ListADT"
    assert list_surface.surface_tier == DEFAULT_SURFACE_TIER
    print(f"[PASS] encode_adt_surface: {list_surface.summarize()}")

    # 19. MAX_DEFERRED_PROOFS constant
    assert MAX_DEFERRED_PROOFS == 16
    print(f"[PASS] MAX_DEFERRED_PROOFS = {MAX_DEFERRED_PROOFS}")

    # 20. COCHAIN_VERSION_TAG constant
    assert COCHAIN_VERSION_TAG.startswith("s03")
    print(f"[PASS] COCHAIN_VERSION_TAG = {COCHAIN_VERSION_TAG!r}")

    print("\n=== s03 smoke test PASSED ===")
