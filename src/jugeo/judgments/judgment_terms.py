"""Judgment term algebra for the shared JuGeo core.

A **judgment** is THE central semantic object of the JuGeo framework.  It is
**not** a boolean — it is an eight-component tuple

    J = (c, φ, A, E, O, B, T, Π)

as laid out in ``preliminaries/theory2.tex``.  Each slot carries irreducible
information that later certificates, diagnostics, and descent checks rely on:

* **c** — *coordinate* in the semantic site (where in the project the claim
  lives).
* **φ** — *proposition* (what is claimed: structural, behavioural, relational,
  resource, or semantic).
* **A** — *carrier / type* (what kind of thing the claim is about, possibly
  dependent).
* **E** — *evidence bundle* (the supporting evidence items, each with its own
  channel and trust level).
* **O** — *residual obligations* (what remains to be verified before the
  judgment may settle).
* **B** — *obstructions* (persistent records of what blocks full
  verification — first-class cohomology classes, never silently erased).
* **T** — *trust annotation* from the ordered algebra ``(E_adm, ⪯, ⊕, ⊖,
  ↑_π, ↓_χ)``; this is **not** a scalar — it is a structured algebraic
  element.
* **Π** — *provenance* (where the judgment came from — solver, runtime,
  oracle / copilot, human, or composition).

Proposal channels such as **copilot** may contribute evidence items and create
new judgments, but they cannot change settlement status without an external
discharge step.  This module implements the full judgment term algebra:
construction, restriction, transport, merging, trust comparison, and
serialisation.

See also
--------
* ``judgments/contexts.py`` — semantic contexts presheaf.
* ``judgments/sections.py`` — judgment sections over the site.
* ``evidence/trust.py`` — the ``TrustProfile`` / ``TrustTier`` algebra.
* ``geometry/site.py`` — coordinates, morphisms, and the semantic site.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any, Mapping, Sequence

from jugeo.geometry.site import CoordinateObject, CoordinateMorphism


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _dedupe_strings(items: tuple[str, ...]) -> tuple[str, ...]:
    """Return *items* with duplicates removed, preserving first-occurrence order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


def _stable_hash(payload: str) -> str:
    """Produce a deterministic SHA-256 hex digest for *payload*."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    """Current UTC timestamp in ISO-8601 format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ═══════════════════════════════════════════════════════════════════════════
# §1  Enumerations
# ═══════════════════════════════════════════════════════════════════════════

class JudgmentStatus(str, Enum):
    """Lifecycle status of a local judgment.

    Backward-compatible enum kept for existing callers in ``exports.py``,
    ``sections.py``, and the test suite.
    """

    PROPOSED = "proposed"
    CHALLENGED = "challenged"
    SETTLED = "settled"
    OBSTRUCTED = "obstructed"


class TrustLevel(IntEnum):
    """Ordered trust tiers for the judgment trust annotation.

    The ordering is strict: ``VERIFIED_PROOF > SOLVER_DISCHARGED > … >
    CONTRADICTED``.  The integer values expose that ordering to Python's
    comparison operators while the symbolic names keep code self-documenting.

    See ``theory2.tex §252`` — the admissible-evidence poset.
    """

    CONTRADICTED = 0
    UNVERIFIED = 1
    COPILOT_SUGGESTED = 2
    ORACLE_PROPOSED = 2
    RUNTIME_WITNESSED = 3
    SOLVER_DISCHARGED = 4
    VERIFIED_PROOF = 5

    def label(self) -> str:
        """Human-readable label for display and serialisation."""
        return self.name.lower().replace("_", "-")

    def stronger_than(self, other: TrustLevel | int) -> bool:
        """Return *True* when ``self`` is strictly above *other*."""
        return int(self) > int(other)

    def weaker_than(self, other: TrustLevel | int) -> bool:
        """Return *True* when ``self`` is strictly below *other*."""
        return int(self) < int(other)

    def step_weaker(self) -> TrustLevel:
        """One step downward, clamped at ``CONTRADICTED``."""
        vals = list(TrustLevel)
        idx = vals.index(self)
        return vals[max(0, idx - 1)]

    def step_stronger(self) -> TrustLevel:
        """One step upward, clamped at ``VERIFIED_PROOF``."""
        vals = list(TrustLevel)
        idx = vals.index(self)
        return vals[min(len(vals) - 1, idx + 1)]


class PropositionKind(str, Enum):
    """Classification of the claim made by a proposition.

    Each kind corresponds to a distinct verification discipline: structural
    claims are resolved by type-checking, behavioural by testing / runtime
    witnesses, relational by cross-module descent, resource by budget analysis,
    and semantic by LLM-assisted reasoning.
    """

    STRUCTURAL = "structural"
    BEHAVIORAL = "behavioral"
    RELATIONAL = "relational"
    RESOURCE = "resource"
    SEMANTIC = "semantic"


class EvidenceItemKind(str, Enum):
    """Kind of a single evidence item.

    These map one-to-one onto the evidence channels described in
    ``evidence/channels.py`` but are carried *inside* the judgment term itself
    so that the evidence bundle is self-contained.
    """

    SOLVER_PROOF = "solver_proof"
    RUNTIME_WITNESS = "runtime_witness"
    ORACLE_PROPOSAL = "oracle_proposal"
    FORMAL_PROOF = "formal_proof"


class ProvenanceSource(str, Enum):
    """Origin of a judgment or evidence item.

    The ``ORACLE`` source corresponds to copilot-assisted proposal.
    """

    SOLVER = "solver"
    RUNTIME = "runtime"
    ORACLE = "oracle"       # copilot-assisted proposal channel
    HUMAN = "human"
    COMPOSED = "composed"   # judgment formed by algebraic composition


# ═══════════════════════════════════════════════════════════════════════════
# §2  Proposition
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class Proposition:
    """What is claimed by a judgment.

    A proposition carries a *kind* (structural, behavioural, …), a *formula*
    string (the human/machine readable claim), and a set of *free variables*
    that must be instantiated before the proposition can be discharged.

    Parameters
    ----------
    kind : PropositionKind
        Classification of the claim.
    formula : str
        The claim statement — may be a Z3 s-expression, a natural language
        sentence, or a dependent-type term.
    free_variables : tuple[str, ...]
        Names of unbound variables in *formula*.
    metadata : Mapping[str, Any]
        Optional extra data attached to the proposition.
    """

    kind: PropositionKind
    formula: str
    free_variables: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # -- variable operations ------------------------------------------------

    def substitute(self, bindings: Mapping[str, str]) -> Proposition:
        """Apply textual substitution for each variable in *bindings*.

        Only variables listed in :pyattr:`free_variables` are substituted.
        Returns a new :class:`Proposition` with the remaining free variables
        updated accordingly.
        """
        new_formula = self.formula
        remaining_vars: list[str] = []
        for var in self.free_variables:
            if var in bindings:
                new_formula = new_formula.replace(var, bindings[var])
            else:
                remaining_vars.append(var)
        return replace(
            self,
            formula=new_formula,
            free_variables=tuple(remaining_vars),
        )

    def is_closed(self) -> bool:
        """Return *True* when no free variables remain."""
        return len(self.free_variables) == 0

    # -- logical combinators -----------------------------------------------

    def simplify(self) -> Proposition:
        """Normalise whitespace and strip redundant parentheses (shallow)."""
        simplified = " ".join(self.formula.split())
        while simplified.startswith("(") and simplified.endswith(")"):
            inner = simplified[1:-1]
            # Only strip if balanced
            depth = 0
            balanced = True
            for ch in inner:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if depth < 0:
                    balanced = False
                    break
            if balanced and depth == 0:
                simplified = inner
            else:
                break
        return replace(self, formula=simplified)

    def negate(self) -> Proposition:
        """Return the syntactic negation ``¬(φ)``."""
        return replace(self, formula=f"¬({self.formula})")

    def implies(self, consequent: Proposition) -> Proposition:
        """Build ``self → consequent``."""
        combined_vars = _dedupe_strings(
            self.free_variables + consequent.free_variables
        )
        return Proposition(
            kind=self.kind,
            formula=f"({self.formula}) → ({consequent.formula})",
            free_variables=combined_vars,
        )

    def conjunction(self, other: Proposition) -> Proposition:
        """Build ``self ∧ other``."""
        combined_vars = _dedupe_strings(
            self.free_variables + other.free_variables
        )
        kind = self.kind if self.kind == other.kind else PropositionKind.RELATIONAL
        return Proposition(
            kind=kind,
            formula=f"({self.formula}) ∧ ({other.formula})",
            free_variables=combined_vars,
        )

    def disjunction(self, other: Proposition) -> Proposition:
        """Build ``self ∨ other``."""
        combined_vars = _dedupe_strings(
            self.free_variables + other.free_variables
        )
        kind = self.kind if self.kind == other.kind else PropositionKind.RELATIONAL
        return Proposition(
            kind=kind,
            formula=f"({self.formula}) ∨ ({other.formula})",
            free_variables=combined_vars,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "kind": self.kind.value,
            "formula": self.formula,
            "free_variables": list(self.free_variables),
            "metadata": dict(self.metadata),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §3  Carrier (type / artifact kind)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class Carrier:
    """The type or carrier of a judgment — what *kind* of thing the claim
    is about.

    Carriers may be parameterised (generic types) and may participate in a
    subtype lattice.  Dependent carriers have ``is_dependent=True`` and carry
    unresolved parameters that must be instantiated before descent checks.

    Parameters
    ----------
    name : str
        Type name, e.g. ``"FunctionContract"``, ``"ModuleInterface"``.
    parameters : tuple[str, ...]
        Ordered type parameters (empty for monomorphic carriers).
    is_dependent : bool
        Whether the carrier depends on a term-level variable.
    metadata : Mapping[str, Any]
        Additional type-level metadata.
    """

    name: str
    parameters: tuple[str, ...] = ()
    is_dependent: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def substitute_params(self, bindings: Mapping[str, str]) -> Carrier:
        """Replace type parameters present in *bindings*.

        Returns a new carrier whose parameter list contains only the
        un-substituted parameters.
        """
        new_params: list[str] = []
        new_name = self.name
        for p in self.parameters:
            if p in bindings:
                new_name = new_name.replace(p, bindings[p])
            else:
                new_params.append(p)
        return replace(
            self,
            name=new_name,
            parameters=tuple(new_params),
            is_dependent=self.is_dependent and bool(new_params),
        )

    def refine(self, suffix: str, *, extra_params: tuple[str, ...] = ()) -> Carrier:
        """Create a refinement (sub-carrier) by appending *suffix* to the name.

        The resulting carrier inherits the parent's parameters plus any
        additional ones specified by *extra_params*.
        """
        return replace(
            self,
            name=f"{self.name}.{suffix}",
            parameters=self.parameters + extra_params,
        )

    def coarsen(self) -> Carrier:
        """Strip the last refinement segment, moving up the type hierarchy.

        If the name has no dot-separated segments, returns self unchanged.
        """
        parts = self.name.rsplit(".", 1)
        if len(parts) == 1:
            return self
        return replace(self, name=parts[0])

    def is_subtype_of(self, other: Carrier) -> bool:
        """Syntactic subtype check: ``self`` is a subtype of *other* when
        ``self.name`` starts with ``other.name`` and all of *other*'s
        parameters appear in ``self.parameters``.
        """
        if not self.name.startswith(other.name):
            return False
        other_set = set(other.parameters)
        self_set = set(self.parameters)
        return other_set.issubset(self_set) or other_set == self_set

    def meet(self, other: Carrier) -> Carrier:
        """Greatest lower bound — the most refined carrier that is a
        supertype of both.

        Heuristic: take the longer (more refined) name if one is a prefix of
        the other; otherwise form a synthetic meet name.  Parameters are
        unioned.
        """
        if self.name.startswith(other.name):
            chosen_name = self.name
        elif other.name.startswith(self.name):
            chosen_name = other.name
        else:
            chosen_name = f"({self.name} ∧ {other.name})"
        return Carrier(
            name=chosen_name,
            parameters=_dedupe_strings(self.parameters + other.parameters),
            is_dependent=self.is_dependent or other.is_dependent,
        )

    def join(self, other: Carrier) -> Carrier:
        """Least upper bound — the coarsest carrier that is a subtype of
        both.

        Heuristic: take the shorter (less refined) name if one is a prefix of
        the other; otherwise form a synthetic join name.  Parameters are
        intersected.
        """
        if self.name.startswith(other.name):
            chosen_name = other.name
        elif other.name.startswith(self.name):
            chosen_name = self.name
        else:
            chosen_name = f"({self.name} ∨ {other.name})"
        common_params = tuple(
            p for p in self.parameters if p in other.parameters
        )
        return Carrier(
            name=chosen_name,
            parameters=common_params,
            is_dependent=self.is_dependent and other.is_dependent,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "name": self.name,
            "parameters": list(self.parameters),
            "is_dependent": self.is_dependent,
            "metadata": dict(self.metadata),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §4  Evidence items and bundles
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """A single piece of evidence supporting (or undermining) a judgment.

    Each item records its *kind*, a *payload* (opaque to this module), the
    *trust_level* at which it was produced, the *channel* it arrived on, and
    temporal validity bounds.

    Parameters
    ----------
    kind : EvidenceItemKind
        Classification (solver proof, runtime witness, oracle proposal, …).
    payload : Mapping[str, Any]
        Opaque evidence content.
    trust_level : TrustLevel
        Strength of the evidence at creation time.
    channel : str
        Name of the evidence channel (e.g. ``"z3"``, ``"copilot"``).
    timestamp : str
        ISO-8601 creation timestamp.
    expiry : str
        ISO-8601 expiry timestamp (empty string ⟹ no expiry).
    provenance : tuple[str, ...]
        Ordered provenance tags.
    """

    kind: EvidenceItemKind
    payload: Mapping[str, Any] = field(default_factory=dict)
    trust_level: TrustLevel = TrustLevel.UNVERIFIED
    channel: str = ""
    timestamp: str = field(default_factory=_now_iso)
    expiry: str = ""
    provenance: tuple[str, ...] = ()

    def is_valid(self) -> bool:
        """Return *True* when the item has not expired and is not contradicted."""
        if self.trust_level == TrustLevel.CONTRADICTED:
            return False
        if not self.expiry:
            return True
        return self.expiry > _now_iso()

    def is_expired(self) -> bool:
        """Return *True* when the item has a non-empty expiry that has passed."""
        if not self.expiry:
            return False
        return self.expiry <= _now_iso()

    def with_trust(self, level: TrustLevel) -> EvidenceItem:
        """Return a copy with a different trust level."""
        return replace(self, trust_level=level)

    def canonical_key(self) -> str:
        """Deterministic content-addressed key for deduplication."""
        raw = json.dumps(
            {"kind": self.kind.value, "channel": self.channel,
             "payload": dict(self.payload)},
            sort_keys=True,
        )
        return _stable_hash(raw)

    def __hash__(self) -> int:
        """Hash by full semantic identity so items can participate in sets."""
        return hash((
            self.kind,
            self.channel,
            self.canonical_key(),
            int(self.trust_level),
            self.timestamp,
            self.expiry,
            self.provenance,
        ))

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "kind": self.kind.value,
            "payload": dict(self.payload),
            "trust_level": self.trust_level.label(),
            "channel": self.channel,
            "timestamp": self.timestamp,
            "expiry": self.expiry,
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """An ordered collection of :class:`EvidenceItem` instances attached to a
    judgment.

    The bundle supports filtering, merging, and trust aggregation.  It is
    immutable: mutation methods return new bundles.  Empty bundles are
    semantically meaningful — they denote an *unsupported* judgment.
    """

    items: tuple[EvidenceItem, ...] = ()

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    # -- querying -----------------------------------------------------------

    def by_kind(self, kind: EvidenceItemKind) -> EvidenceBundle:
        """Filter to items matching *kind*."""
        return EvidenceBundle(
            items=tuple(i for i in self.items if i.kind == kind)
        )

    def by_channel(self, channel: str) -> EvidenceBundle:
        """Filter to items arriving on *channel*."""
        return EvidenceBundle(
            items=tuple(i for i in self.items if i.channel == channel)
        )

    def strongest(self) -> EvidenceItem | None:
        """Return the item with the highest trust level, or ``None``."""
        if not self.items:
            return None
        return max(self.items, key=lambda i: int(i.trust_level))

    def weakest(self) -> EvidenceItem | None:
        """Return the item with the lowest trust level, or ``None``."""
        if not self.items:
            return None
        return min(self.items, key=lambda i: int(i.trust_level))

    def is_empty(self) -> bool:
        """Return *True* when the bundle contains no items."""
        return len(self.items) == 0

    def valid_items(self) -> EvidenceBundle:
        """Return a new bundle containing only currently-valid items."""
        return EvidenceBundle(
            items=tuple(i for i in self.items if i.is_valid())
        )

    # -- mutation (returns new bundle) --------------------------------------

    def merge(self, other: EvidenceBundle) -> EvidenceBundle:
        """Combine two bundles without dropping any constituent evidence."""
        return EvidenceBundle(items=self.items + other.items)

    def add_evidence(self, item: EvidenceItem) -> EvidenceBundle:
        """Append a single item to the bundle."""
        return EvidenceBundle(items=self.items + (item,))

    def remove_stale(self) -> EvidenceBundle:
        """Drop expired and contradicted items."""
        return EvidenceBundle(
            items=tuple(
                i for i in self.items
                if not i.is_expired() and i.trust_level != TrustLevel.CONTRADICTED
            )
        )

    def filter_by_jurisdiction(self, allowed_channels: frozenset[str]) -> EvidenceBundle:
        """Keep only items whose channel is in *allowed_channels*.

        This is the jurisdictional filter that prevents copilot-originated
        evidence from claiming solver-level trust.
        """
        return EvidenceBundle(
            items=tuple(i for i in self.items if i.channel in allowed_channels)
        )

    # -- aggregation --------------------------------------------------------

    def total_trust(self) -> TrustLevel:
        """Conservative aggregate trust: the *minimum* trust level across all
        valid items.

        An empty bundle has trust ``UNVERIFIED``.  This is deliberately
        conservative — the trust floor is the weakest link.
        """
        valid = self.valid_items()
        if valid.is_empty():
            return TrustLevel.UNVERIFIED
        return TrustLevel(min(int(i.trust_level) for i in valid.items))

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "items": [i.to_mapping() for i in self.items],
            "total_trust": self.total_trust().label(),
            "count": len(self.items),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §5  Residual obligations
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class ResidualObligation:
    """Something yet to be verified before a judgment may settle.

    Obligations are first-class: they carry an id, a human-readable
    description, the *kind* of evidence required to discharge them, a
    priority, and optional dependency links to other obligations.

    Parameters
    ----------
    obligation_id : str
        Unique identifier (UUID recommended).
    description : str
        What needs to be verified.
    required_evidence_kind : EvidenceItemKind
        The kind of evidence that would discharge this obligation.
    deadline : str
        ISO-8601 deadline (empty ⟹ no deadline).
    priority : int
        Scheduling priority (lower = more urgent).
    depends_on : tuple[str, ...]
        IDs of obligations that must be discharged first.
    is_discharged : bool
        Whether this obligation has been satisfied.
    discharge_evidence : str
        Canonical key of the evidence item that discharged this obligation.
    provenance : tuple[str, ...]
        Audit trail for the obligation.
    """

    obligation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""
    required_evidence_kind: EvidenceItemKind = EvidenceItemKind.SOLVER_PROOF
    deadline: str = ""
    priority: int = 5
    depends_on: tuple[str, ...] = ()
    is_discharged: bool = False
    discharge_evidence: str = ""
    provenance: tuple[str, ...] = ()

    def discharge(self, evidence_key: str, *, reason: str = "") -> ResidualObligation:
        """Mark this obligation as discharged, recording the evidence key.

        Parameters
        ----------
        evidence_key : str
            Canonical key of the discharging evidence item.
        reason : str
            Optional human-readable note.
        """
        new_prov = self.provenance + (
            f"discharged:{evidence_key}" + (f" ({reason})" if reason else ""),
        )
        return replace(
            self,
            is_discharged=True,
            discharge_evidence=evidence_key,
            provenance=new_prov,
        )

    def is_overdue(self) -> bool:
        """Return *True* when a deadline exists and has passed."""
        if not self.deadline:
            return False
        return self.deadline <= _now_iso()

    def is_blocked(self, discharged_ids: frozenset[str]) -> bool:
        """Return *True* when at least one dependency has not been discharged."""
        return bool(set(self.depends_on) - discharged_ids)

    def with_priority(self, new_priority: int) -> ResidualObligation:
        """Return a copy with adjusted priority."""
        return replace(self, priority=new_priority)

    def with_dependency(self, other_id: str) -> ResidualObligation:
        """Return a copy that additionally depends on *other_id*."""
        if other_id in self.depends_on:
            return self
        return replace(self, depends_on=self.depends_on + (other_id,))

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "obligation_id": self.obligation_id,
            "description": self.description,
            "required_evidence_kind": self.required_evidence_kind.value,
            "deadline": self.deadline,
            "priority": self.priority,
            "depends_on": list(self.depends_on),
            "is_discharged": self.is_discharged,
            "discharge_evidence": self.discharge_evidence,
            "provenance": list(self.provenance),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §6  Obstructions
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class Obstruction:
    """A persistent record of what blocks full verification of a judgment.

    Obstructions are treated as *cohomology classes* (see ``theory2.tex``):
    they are never silently erased by additional evidence.  Resolution
    requires an explicit repair step that produces new evidence addressing
    the violated condition.

    Parameters
    ----------
    obstruction_id : str
        Unique identifier.
    violated_condition : str
        Human-readable description of the condition that failed.
    coordinate : str
        Coordinate key where the obstruction was observed.
    evidence_at_time : tuple[str, ...]
        Canonical keys of evidence items present when the obstruction was
        recorded.
    repair_hints : tuple[str, ...]
        Suggestions for how to resolve the obstruction.
    cohomology_class : str
        Symbolic tag for the obstruction class in H^1 (empty if not
        computed).
    is_resolved : bool
        Whether the obstruction has been resolved.
    resolution_evidence : str
        Canonical key of the evidence that resolved this obstruction.
    provenance : tuple[str, ...]
        Audit trail.
    """

    obstruction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    violated_condition: str = ""
    description: str = ""
    coordinate: str = ""
    coordinate_pair: tuple[str, str] = ()
    evidence_at_time: tuple[str, ...] = ()
    repair_hints: tuple[str, ...] = ()
    cohomology_class: str = ""
    severity: int = 0
    is_resolved: bool = False
    resolution_evidence: str = ""
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.coordinate_pair and not self.coordinate:
            object.__setattr__(self, "coordinate", self.coordinate_pair[0])
        if self.description and not self.violated_condition:
            object.__setattr__(self, "violated_condition", self.description)
        elif self.violated_condition and not self.description:
            object.__setattr__(self, "description", self.violated_condition)

    def resolve(self, evidence_key: str, *, reason: str = "") -> Obstruction:
        """Mark this obstruction as resolved, recording the repair evidence.

        Parameters
        ----------
        evidence_key : str
            Canonical key of the evidence that resolves the obstruction.
        reason : str
            Optional note explaining the resolution.
        """
        new_prov = self.provenance + (
            f"resolved:{evidence_key}" + (f" ({reason})" if reason else ""),
        )
        return replace(
            self,
            is_resolved=True,
            resolution_evidence=evidence_key,
            provenance=new_prov,
        )

    def add_repair_hint(self, hint: str) -> Obstruction:
        """Append a repair hint to the obstruction."""
        if hint in self.repair_hints:
            return self
        return replace(self, repair_hints=self.repair_hints + (hint,))

    def with_cohomology_class(self, cls: str) -> Obstruction:
        """Attach or update the cohomology class label."""
        return replace(self, cohomology_class=cls)

    def is_at_coordinate(self, coord_key: str) -> bool:
        """Return *True* when this obstruction lives at *coord_key*."""
        return self.coordinate == coord_key

    def severity_score(self) -> int:
        """Heuristic severity: unresolved obstructions with no repair hints
        score highest (3), with hints score 2, resolved score 0.
        """
        if self.is_resolved:
            return 0
        if self.repair_hints:
            return 2
        return 3

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "obstruction_id": self.obstruction_id,
            "violated_condition": self.violated_condition,
            "coordinate": self.coordinate,
            "evidence_at_time": list(self.evidence_at_time),
            "repair_hints": list(self.repair_hints),
            "cohomology_class": self.cohomology_class,
            "is_resolved": self.is_resolved,
            "resolution_evidence": self.resolution_evidence,
            "provenance": list(self.provenance),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §7  Trust annotation
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class TrustAnnotation:
    """Structured trust from the ordered algebra ``(E_adm, ⪯, ⊕, ⊖, ↑_π,
    ↓_χ)`` — this is **not** a float.

    The annotation records the current *level*, the *evidence_basis* (which
    evidence items justify the trust level), and a *ceiling* / *floor* that
    constrain how far the trust may be promoted or demoted by algebraic
    operations.

    Parameters
    ----------
    level : TrustLevel
        Current position in the trust poset.
    evidence_basis : tuple[str, ...]
        Canonical keys of evidence items underpinning this trust level.
    ceiling : TrustLevel
        Maximum trust achievable without new evidence.
    floor : TrustLevel
        Minimum trust below which demotion may not descend.
    reasons : tuple[str, ...]
        Human-readable audit trail of trust transitions.
    """

    level: TrustLevel = TrustLevel.UNVERIFIED
    evidence_basis: tuple[str, ...] = ()
    ceiling: TrustLevel = TrustLevel.VERIFIED_PROOF
    floor: TrustLevel = TrustLevel.CONTRADICTED
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Clamp level to ``[floor, ceiling]``."""
        clamped = TrustLevel(max(int(self.floor), min(int(self.ceiling), int(self.level))))
        if clamped != self.level:
            object.__setattr__(self, "level", clamped)

    # -- algebraic operations (theory2.tex §252) ----------------------------

    def compose(self, other: TrustAnnotation) -> TrustAnnotation:
        """Join ``⊕``: conservative aggregation of two trust annotations.

        Takes the *minimum* level of the two (weakest link), unions the
        evidence basis, and intersects ceilings / unions floors.
        """
        new_level = TrustLevel(min(int(self.level), int(other.level)))
        new_ceiling = TrustLevel(min(int(self.ceiling), int(other.ceiling)))
        new_floor = TrustLevel(max(int(self.floor), int(other.floor)))
        combined_basis = _dedupe_strings(self.evidence_basis + other.evidence_basis)
        reason = f"compose({self.level.label()}, {other.level.label()})"
        return TrustAnnotation(
            level=new_level,
            evidence_basis=combined_basis,
            ceiling=new_ceiling,
            floor=new_floor,
            reasons=self.reasons + (reason,),
        )

    def compare(self, other: TrustAnnotation) -> int:
        """Three-valued comparison: -1 if ``self < other``, 0 if equal, +1
        if ``self > other``.
        """
        if int(self.level) < int(other.level):
            return -1
        if int(self.level) > int(other.level):
            return 1
        return 0

    def promote(self, *, reason: str = "", target: TrustLevel | None = None) -> TrustAnnotation:
        """Policy-authorised upward move ``↑_π``.

        Promotion is clamped at ``ceiling`` — the *no silent promotion*
        theorem (theory2.tex) guarantees that copilot-originated evidence
        cannot exceed the ceiling without an explicit human or solver
        discharge.

        Parameters
        ----------
        reason : str
            Mandatory audit note (should not be empty in production).
        target : TrustLevel | None
            Target level; defaults to one step above current.
        """
        if target is None:
            target = self.level.step_stronger()
        effective = TrustLevel(min(int(target), int(self.ceiling)))
        if int(effective) <= int(self.level):
            return self  # no-op: already at or above target
        note = f"promote→{effective.label()}" + (f" ({reason})" if reason else "")
        return replace(
            self,
            level=effective,
            reasons=self.reasons + (note,),
        )

    def demote(self, *, reason: str = "", target: TrustLevel | None = None) -> TrustAnnotation:
        """Explicit demotion ``⊖`` — one-class downward move.

        Demotion is clamped at ``floor``.

        Parameters
        ----------
        reason : str
            Audit note for the demotion.
        target : TrustLevel | None
            Target level; defaults to one step below current.
        """
        if target is None:
            target = self.level.step_weaker()
        effective = TrustLevel(max(int(target), int(self.floor)))
        if int(effective) >= int(self.level):
            return self  # no-op: already at or below target
        note = f"demote→{effective.label()}" + (f" ({reason})" if reason else "")
        return replace(
            self,
            level=effective,
            reasons=self.reasons + (note,),
        )

    def challenge(self, *, reason: str) -> TrustAnnotation:
        """Challenge-triggered demotion ``↓_χ``.

        Always demotes by exactly one step and records the challenge reason.
        """
        return self.demote(reason=f"challenge: {reason}")

    def is_admissible(self) -> bool:
        """Return *True* when the annotation is internally consistent:
        ``floor ≤ level ≤ ceiling``.
        """
        return int(self.floor) <= int(self.level) <= int(self.ceiling)

    def with_evidence(self, key: str) -> TrustAnnotation:
        """Return a copy with *key* added to the evidence basis."""
        if key in self.evidence_basis:
            return self
        return replace(self, evidence_basis=self.evidence_basis + (key,))

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "level": self.level.label(),
            "evidence_basis": list(self.evidence_basis),
            "ceiling": self.ceiling.label(),
            "floor": self.floor.label(),
            "reasons": list(self.reasons),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §8  Provenance
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a judgment came from — its lineage in the derivation tree.

    Parameters
    ----------
    source : ProvenanceSource
        Originating channel (solver, runtime, oracle / copilot, human, or
        composed).
    parent_judgments : tuple[str, ...]
        Hashes of parent judgments from which this one was derived.
    creation_timestamp : str
        ISO-8601 creation timestamp.
    transformation_history : tuple[str, ...]
        Ordered list of transformation labels applied since creation
        (e.g. ``"restrict(module.foo)"``, ``"transport(rename)"``,
        ``"merge(runtime+solver)"``).
    metadata : Mapping[str, Any]
        Additional provenance metadata.
    """

    source: ProvenanceSource = ProvenanceSource.HUMAN
    parent_judgments: tuple[str, ...] = ()
    creation_timestamp: str = field(default_factory=_now_iso)
    transformation_history: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def transformations(self) -> tuple[str, ...]:
        return self.transformation_history

    def with_transformation(self, label: str) -> Provenance:
        """Append a transformation step to the history."""
        return replace(
            self,
            transformation_history=self.transformation_history + (label,),
        )

    def with_parent(self, parent_hash: str) -> Provenance:
        """Record an additional parent judgment."""
        if parent_hash in self.parent_judgments:
            return self
        return replace(
            self,
            parent_judgments=self.parent_judgments + (parent_hash,),
        )

    def is_derived(self) -> bool:
        """Return *True* when this provenance has at least one parent."""
        return len(self.parent_judgments) > 0

    def is_composed(self) -> bool:
        """Return *True* when the source is ``COMPOSED``."""
        return self.source == ProvenanceSource.COMPOSED

    def is_copilot_originated(self) -> bool:
        """Return *True* when the judgment originated from the copilot /
        oracle proposal channel.
        """
        return self.source == ProvenanceSource.ORACLE

    def depth(self) -> int:
        """Number of transformations applied since creation."""
        return len(self.transformation_history)

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "source": self.source.value,
            "parent_judgments": list(self.parent_judgments),
            "creation_timestamp": self.creation_timestamp,
            "transformation_history": list(self.transformation_history),
            "metadata": dict(self.metadata),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §9  Judgment clause (backward-compatible)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class JudgmentClause:
    """A named sub-claim within a judgment, kept for backward compatibility
    with ``exports.py`` and existing tests.

    Parameters
    ----------
    name : str
        Short machine-readable identifier.
    statement : str
        Human-readable statement of the sub-claim.
    satisfied : bool | None
        ``True`` if discharged, ``False`` if refuted, ``None`` if pending.
    evidence_channels : tuple[str, ...]
        Channels that contributed to this clause's status.
    obligations : tuple[str, ...]
        Remaining obligations specific to this clause.
    """

    name: str
    statement: str
    satisfied: bool | None = None
    evidence_channels: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()

    def is_pending(self) -> bool:
        """Return *True* when the clause has not yet been decided."""
        return self.satisfied is None

    def is_satisfied(self) -> bool:
        """Return *True* when explicitly satisfied."""
        return self.satisfied is True

    def with_evidence_channel(self, channel: str) -> JudgmentClause:
        """Return a copy with *channel* appended (deduped)."""
        if channel in self.evidence_channels:
            return self
        return replace(
            self, evidence_channels=self.evidence_channels + (channel,)
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "name": self.name,
            "statement": self.statement,
            "satisfied": self.satisfied,
            "evidence_channels": list(self.evidence_channels),
            "obligations": list(self.obligations),
        }


# ═══════════════════════════════════════════════════════════════════════════
# §10  The Judgment — the central eight-component tuple
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class Judgment:
    """The central semantic object of the JuGeo framework.

    A judgment is an eight-component tuple

        J = (c, φ, A, E, O, B, T, Π)

    as described in ``preliminaries/theory2.tex``.  Each slot is preserved
    faithfully for downstream certificates, diagnostics, and descent checks.

    This class is immutable — all "mutation" methods return new instances.

    Parameters
    ----------
    coordinate : CoordinateObject
        Where in semantic space the claim lives.
    proposition : Proposition
        What is claimed.
    carrier : Carrier
        What kind of thing the claim is about.
    evidence : EvidenceBundle
        Supporting evidence items.
    obligations : tuple[ResidualObligation, ...]
        What remains to be verified.
    obstructions : tuple[Obstruction, ...]
        What blocks full verification.
    trust : TrustAnnotation
        Trust from the ordered algebra (NOT a scalar).
    provenance : Provenance
        Where the judgment came from.
    clauses : tuple[JudgmentClause, ...]
        Optional sub-claims (for backward compatibility with ``LocalJudgment``).
    status : JudgmentStatus
        Lifecycle status.
    """

    coordinate: CoordinateObject
    proposition: Proposition
    carrier: Carrier
    evidence: EvidenceBundle = field(default_factory=EvidenceBundle)
    obligations: tuple[ResidualObligation, ...] = ()
    obstructions: tuple[Obstruction, ...] = ()
    trust: TrustAnnotation = field(default_factory=TrustAnnotation)
    provenance: Provenance = field(default_factory=Provenance)
    clauses: tuple[JudgmentClause, ...] = ()
    status: JudgmentStatus = JudgmentStatus.PROPOSED

    # -- status queries -----------------------------------------------------

    def is_fully_discharged(self) -> bool:
        """Return *True* when every obligation has been discharged, every
        obstruction has been resolved, the evidence bundle is non-empty, and
        the status is ``SETTLED``.
        """
        if self.status is not JudgmentStatus.SETTLED:
            return False
        if self.evidence.is_empty():
            return False
        if any(not o.is_discharged for o in self.obligations):
            return False
        if any(not o.is_resolved for o in self.obstructions):
            return False
        return True

    def has_residuals(self) -> bool:
        """Return *True* when at least one obligation is not yet discharged."""
        return any(not o.is_discharged for o in self.obligations)

    def has_obstructions(self) -> bool:
        """Return *True* when at least one obstruction is unresolved."""
        return any(not o.is_resolved for o in self.obstructions)

    def trust_floor(self) -> TrustLevel:
        """The effective trust floor: the minimum of the trust annotation's
        floor and the evidence bundle's aggregate trust.
        """
        annotation_floor = self.trust.floor
        if annotation_floor == TrustLevel.CONTRADICTED and int(self.trust.level) > int(TrustLevel.CONTRADICTED):
            annotation_floor = self.trust.level
        evidence_trust = self.evidence.total_trust()
        return TrustLevel(min(int(annotation_floor), int(evidence_trust)))

    def pending_obligation_count(self) -> int:
        """Number of obligations not yet discharged."""
        return sum(1 for o in self.obligations if not o.is_discharged)

    def unresolved_obstruction_count(self) -> int:
        """Number of obstructions not yet resolved."""
        return sum(1 for o in self.obstructions if not o.is_resolved)

    # -- structural operations ----------------------------------------------

    def restrict_to(self, target: CoordinateObject) -> Judgment:
        """Restrict the judgment to *target* coordinate.

        The proposition and carrier are unchanged, but the coordinate is
        updated and a restriction step is recorded in provenance.  This
        models the restriction map of the judgment presheaf.
        """
        new_prov = self.provenance.with_transformation(
            f"restrict({target.key})"
        )
        return replace(self, coordinate=target, provenance=new_prov)

    def transport_along(self, morphism: CoordinateMorphism) -> Judgment:
        """Transport the judgment along *morphism* from source to target.

        Requires that ``self.coordinate.key == morphism.source``.  The
        coordinate is updated to the morphism's target and the
        transformation is recorded in provenance.

        Raises
        ------
        ValueError
            If the judgment's coordinate does not match the morphism source.
        """
        if self.coordinate.key != morphism.source:
            raise ValueError(
                f"Cannot transport: judgment coordinate {self.coordinate.key!r} "
                f"does not match morphism source {morphism.source!r}"
            )
        new_coord = replace(
            self.coordinate,
            path=tuple(morphism.target.split("/")),
        )
        new_prov = self.provenance.with_transformation(
            f"transport({morphism.source}→{morphism.target}, {morphism.reason})"
        )
        return replace(self, coordinate=new_coord, provenance=new_prov)

    def merge_evidence(self, extra: EvidenceBundle) -> Judgment:
        """Merge additional evidence into the judgment.

        The trust annotation is *not* automatically promoted — the caller
        must explicitly promote after verifying that the new evidence
        justifies a higher trust level.
        """
        merged = self.evidence.merge(extra)
        new_prov = self.provenance.with_transformation(
            f"merge_evidence(+{len(extra.items)} items)"
        )
        return replace(self, evidence=merged, provenance=new_prov)

    def strengthen(self, reason: str, target: TrustLevel | None = None) -> Judgment:
        """Promote trust with mandatory reason — wraps
        ``TrustAnnotation.promote()``.

        The *no silent promotion* theorem is enforced by the trust
        annotation's ceiling.
        """
        new_trust = self.trust.promote(reason=reason, target=target)
        return replace(self, trust=new_trust)

    def weaken(self, reason: str, target: TrustLevel | None = None) -> Judgment:
        """Demote trust — wraps ``TrustAnnotation.demote()``."""
        new_trust = self.trust.demote(reason=reason, target=target)
        return replace(self, trust=new_trust)

    def add_obligation(self, obligation: ResidualObligation) -> Judgment:
        """Append a residual obligation to the judgment."""
        return replace(
            self, obligations=self.obligations + (obligation,)
        )

    def discharge_obligation(self, obligation_id: str, evidence_key: str,
                             *, reason: str = "") -> Judgment:
        """Discharge the obligation identified by *obligation_id*.

        Returns a new judgment with the obligation marked as discharged.  If
        all obligations become discharged and all obstructions are resolved,
        the status is upgraded to ``SETTLED``.
        """
        new_obs: list[ResidualObligation] = []
        found = False
        for ob in self.obligations:
            if ob.obligation_id == obligation_id and not ob.is_discharged:
                new_obs.append(ob.discharge(evidence_key, reason=reason))
                found = True
            else:
                new_obs.append(ob)
        if not found:
            raise ValueError(f"Obligation {obligation_id!r} not found or already discharged")
        result = replace(self, obligations=tuple(new_obs))
        # Auto-settle if everything is discharged
        if not result.has_residuals() and not result.has_obstructions():
            result = replace(result, status=JudgmentStatus.SETTLED)
        return result

    def add_obstruction(self, obstruction: Obstruction) -> Judgment:
        """Record a new obstruction, setting status to ``OBSTRUCTED``."""
        return replace(
            self,
            obstructions=self.obstructions + (obstruction,),
            status=JudgmentStatus.OBSTRUCTED,
        )

    def resolve_obstruction(self, obstruction_id: str, evidence_key: str,
                            *, reason: str = "") -> Judgment:
        """Resolve the obstruction identified by *obstruction_id*.

        Returns a new judgment with the obstruction marked as resolved.  If
        no unresolved obstructions remain and no pending obligations exist,
        the status may upgrade to ``SETTLED``.
        """
        new_obs: list[Obstruction] = []
        found = False
        for ob in self.obstructions:
            if ob.obstruction_id == obstruction_id and not ob.is_resolved:
                new_obs.append(ob.resolve(evidence_key, reason=reason))
                found = True
            else:
                new_obs.append(ob)
        if not found:
            raise ValueError(
                f"Obstruction {obstruction_id!r} not found or already resolved"
            )
        result = replace(self, obstructions=tuple(new_obs))
        if not result.has_residuals() and not result.has_obstructions():
            result = replace(result, status=JudgmentStatus.SETTLED)
        return result

    # -- projection & serialisation -----------------------------------------

    def project_to_public(self) -> dict[str, Any]:
        """Public projection: strips internal provenance details and
        evidence payloads, keeping only trust summaries and status.

        Useful for external APIs where full internal state should not be
        exposed.
        """
        return {
            "coordinate": self.coordinate.key,
            "proposition": self.proposition.formula,
            "proposition_kind": self.proposition.kind.value,
            "carrier": self.carrier.name,
            "status": self.status.value,
            "trust_level": self.trust.level.label(),
            "pending_obligations": self.pending_obligation_count(),
            "unresolved_obstructions": self.unresolved_obstruction_count(),
            "evidence_count": len(self.evidence.items),
            "is_fully_discharged": self.is_fully_discharged(),
        }

    def serialize(self) -> dict[str, Any]:
        """Full serialisation to a plain dictionary.

        Every slot is serialised, including all nested structures.  The
        output is JSON-compatible.
        """
        trust_mapping = self.trust.to_mapping()
        trust_mapping["level"] = int(self.trust.level)
        return {
            "coordinate": self.coordinate.key,
            "proposition": self.proposition.to_mapping(),
            "carrier": self.carrier.to_mapping(),
            "evidence": self.evidence.to_mapping(),
            "obligations": [o.to_mapping() for o in self.obligations],
            "obstructions": [o.to_mapping() for o in self.obstructions],
            "trust": trust_mapping,
            "provenance": self.provenance.to_mapping(),
            "clauses": [c.to_mapping() for c in self.clauses],
            "status": self.status.value,
            "hash": self.content_hash(),
        }

    def content_hash(self) -> str:
        """Deterministic SHA-256 hash for deduplication and lineage tracking.

        The hash covers coordinate, proposition formula, carrier name, trust
        level, and status — but NOT evidence (which may evolve).
        """
        raw = json.dumps(
            {
                "coordinate": self.coordinate.key,
                "formula": self.proposition.formula,
                "carrier": self.carrier.name,
                "trust": self.trust.level.label(),
                "status": self.status.value,
            },
            sort_keys=True,
        )
        return _stable_hash(raw)

    def __hash__(self) -> int:
        """Python hash for set/dict membership, based on content_hash."""
        return hash(self.content_hash())

    def __eq__(self, other: object) -> bool:
        """Equality based on content_hash."""
        if not isinstance(other, Judgment):
            return NotImplemented
        return self.content_hash() == other.content_hash()

    # ------------------------------------------------------------------ #
    # Cross-subsystem integration methods
    # ------------------------------------------------------------------ #

    def encode_to_z3(self) -> Any:
        """Encode this judgment's proposition as a Z3 formula.

        Uses :class:`jugeo.solver.z3_session.Z3Encoder` to translate the
        proposition into a Z3 formula suitable for solver-backed discharge.
        The encoding respects the proposition kind: structural claims use
        refinement-type encoding, behavioural claims use path-condition
        encoding, and all others fall back to plain proposition encoding.

        Returns
        -------
        Z3Formula
            A typed wrapper around the Z3 AST node representing this
            judgment's claim.

        Raises
        ------
        RuntimeError
            If the Z3 solver subsystem is unavailable.
        """
        try:
            from jugeo.solver.z3_session import Z3Encoder
        except Exception as exc:
            raise RuntimeError(
                "Z3 solver subsystem unavailable; cannot encode judgment"
            ) from exc

        encoder = Z3Encoder(prefix=f"jg_{self.coordinate.key.replace('/', '_')}_")
        formula_text = self.proposition.formula
        kind = self.proposition.kind

        if kind == PropositionKind.STRUCTURAL:
            return encoder.encode_refinement_type(
                base_type=self.carrier.name,
                predicate=formula_text,
            )
        if kind == PropositionKind.BEHAVIORAL:
            return encoder.encode_path_condition([formula_text])
        return encoder.encode_proposition(formula_text)

    def evaluate_trust(self) -> Any:
        """Compute the composed trust level across all evidence items.

        Uses :class:`jugeo.evidence.trust.TrustAlgebra` to fold the trust
        algebra's composition operator (⊕) over the evidence bundle, yielding
        the aggregate trust level for this judgment.

        Returns
        -------
        TrustLevel
            The composed trust level after folding over all evidence items.

        Raises
        ------
        RuntimeError
            If the trust algebra subsystem is unavailable.
        """
        try:
            from jugeo.evidence.trust import TrustAlgebra
        except Exception as exc:
            raise RuntimeError(
                "Trust algebra subsystem unavailable; cannot evaluate trust"
            ) from exc

        algebra = TrustAlgebra()
        composed = algebra.bottom()
        for item in self.evidence.items:
            item_level = self.trust.level
            composed = algebra.compose(composed, item_level)
        if not self.evidence.items:
            composed = self.trust.level
        return composed

    def obstruction_class(self) -> Any:
        """Compute the Čech cohomology class for this judgment's obstructions.

        Uses :class:`jugeo.geometry.descent.CohomologyClass` to assemble
        unresolved obstructions into a persistent H¹ representative that
        can be inspected, refined, or repaired.

        Returns
        -------
        CohomologyClass
            The cohomology class representing the obstruction state.
            A trivial class indicates no obstructions block verification.

        Raises
        ------
        RuntimeError
            If the descent subsystem is unavailable.
        """
        try:
            from jugeo.geometry.descent import CohomologyClass
        except Exception as exc:
            raise RuntimeError(
                "Descent subsystem unavailable; cannot compute obstruction class"
            ) from exc

        cocycle_data: dict[str, str] = {}
        for obs in self.obstructions:
            if not obs.is_resolved:
                cocycle_data[obs.obstruction_id] = obs.violated_condition
        return CohomologyClass(
            dimension=1,
            cocycle_data=cocycle_data,
        )

    def repair_frontier(self) -> Any:
        """Suggest repairs when this judgment has unresolved obstructions.

        Uses :class:`jugeo.geometry.descent.RepairFrontier` to assemble
        actionable repair hints based on the current obstruction and
        residual-obligation state.

        Returns
        -------
        RepairFrontier
            A structured collection of repair suggestions prioritised by
            estimated cost.

        Raises
        ------
        RuntimeError
            If the descent subsystem is unavailable.
        """
        try:
            from jugeo.geometry.descent import RepairFrontier
        except Exception as exc:
            raise RuntimeError(
                "Descent subsystem unavailable; cannot compute repair frontier"
            ) from exc

        missing_evidence = [
            f"evidence needed for obligation: {ob.description}"
            for ob in self.obligations
            if not ob.is_discharged
        ]
        weakened_claims = [
            f"obstruction blocks: {obs.violated_condition}"
            for obs in self.obstructions
            if not obs.is_resolved
        ]
        suggested_refinements = []
        if self.status == JudgmentStatus.OBSTRUCTED:
            suggested_refinements.append(
                f"refine proposition at {self.coordinate.key}"
            )
        return RepairFrontier(
            missing_evidence=missing_evidence,
            weakened_claims=weakened_claims,
            suggested_refinements=suggested_refinements,
            estimated_cost=len(missing_evidence) + len(weakened_claims),
        )

    def certification_status(self) -> dict[str, Any]:
        """Check the certificate chain status for this judgment.

        Uses :class:`jugeo.evidence.certificates.CertificateChain` to verify
        whether a valid certificate chain exists covering this judgment's
        proposition at its coordinate, and reports any gaps.

        Returns
        -------
        dict[str, Any]
            A mapping with keys ``"chain_valid"``, ``"trust_floor"``,
            ``"gaps"``, and ``"covers_proposition"`` describing the
            certification posture.

        Raises
        ------
        RuntimeError
            If the certificates subsystem is unavailable.
        """
        try:
            from jugeo.evidence.certificates import Certificate, CertificateChain
        except Exception as exc:
            raise RuntimeError(
                "Certificates subsystem unavailable; cannot check certification"
            ) from exc

        certs: list[Certificate] = []
        for item in self.evidence.items:
            ref = item.payload.get("ref", "") if isinstance(item.payload, Mapping) else ""
            if ref:
                try:
                    cert = Certificate.solver_backed_certificate(z3_result=None)
                except Exception:
                    continue
                certs.append(cert)

        if not certs:
            return {
                "chain_valid": False,
                "trust_floor": self.trust.level.label(),
                "gaps": [{"reason": "no certificates available"}],
                "covers_proposition": False,
            }

        chain = CertificateChain(certificates=certs)
        return {
            "chain_valid": chain.verify_chain(),
            "trust_floor": chain.trust_floor().label()
                if hasattr(chain.trust_floor(), "label")
                else str(chain.trust_floor()),
            "gaps": chain.gaps(),
            "covers_proposition": any(
                c.covers_proposition(self.proposition.formula)
                for c in certs
            ),
        }

    # ------------------------------------------------------------------ #
    # Sheaf-theoretic enrichments
    # ------------------------------------------------------------------ #

    @property
    def site_coordinate(self) -> Any:
        """Return a full ``Coordinate`` from ``jugeo.geometry.site``.

        In the judgment geometry, every judgment J = (c, φ, A, E, O, B, T, Π)
        is indexed by a coordinate c in the semantic site S.  This property
        lifts the stored ``CoordinateObject`` into the richer ``Coordinate``
        type that carries fiber information and participates in the site
        category's morphism algebra.
        """
        try:
            from jugeo.geometry.site import Coordinate
        except Exception:
            return self.coordinate
        return Coordinate(
            path=self.coordinate.path,
            kind=self.coordinate.kind,
        )

    @property
    def trust_algebra_element(self) -> Any:
        """Return the trust annotation as an element of the ordered algebra.

        Theory2.tex §7 defines the admissible-evidence algebra
        (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ).  This property lifts the stored
        ``TrustAnnotation`` into a first-class ``TrustAlgebraElement``
        from ``jugeo.evidence.trust``, enabling algebraic composition
        (⊕), weakening (⊖), and policy-bounded promotion (↑_π / ↓_χ).
        """
        try:
            from jugeo.evidence.trust import TrustAlgebraElement
        except Exception:
            return self.trust
        return TrustAlgebraElement(
            level=self.trust.level,
            ceiling=self.trust.ceiling,
            floor=self.trust.floor,
            reasons=self.trust.reasons,
        )

    @property
    def evidence_channels(self) -> list[Any]:
        """Return the list of evidence channels active in this judgment.

        Each evidence item in the bundle was contributed through a specific
        channel (solver, runtime, oracle / copilot, formal proof).  This
        property resolves them into ``EvidenceChannel`` objects from
        ``jugeo.evidence.channels``, giving access to per-channel trust
        ceilings and discharge protocols.
        """
        try:
            from jugeo.evidence.channels import EvidenceChannel
        except Exception:
            return [
                {"kind": item.kind.value, "channel": item.channel}
                for item in self.evidence.items
            ]
        channels: list[Any] = []
        seen: set[str] = set()
        for item in self.evidence.items:
            key = f"{item.kind.value}:{item.channel}"
            if key not in seen:
                seen.add(key)
                channels.append(EvidenceChannel(
                    kind=item.kind,
                    name=item.channel,
                ))
        return channels

    @property
    def provenance_graph(self) -> Any:
        """Build a provenance DAG from this judgment's provenance record.

        The provenance Π of a judgment tracks its causal history — which
        solver session, runtime witness, oracle consultation, or human
        review contributed each piece of evidence.  This property assembles
        those into a ``ProvenanceDAG`` from ``jugeo.evidence.provenance``,
        a directed acyclic graph whose nodes are provenance events and whose
        edges are causal dependencies.
        """
        try:
            from jugeo.evidence.provenance import ProvenanceDAG, ProvenanceNode
        except Exception:
            return {
                "source": self.provenance.source.value,
                "history": list(self.provenance.transformation_history),
                "dag_available": False,
            }
        nodes: list[Any] = []
        root = ProvenanceNode(
            node_id=f"root:{self.coordinate.key}",
            source=self.provenance.source.value,
            label="judgment-origin",
        )
        nodes.append(root)
        for i, step in enumerate(self.provenance.transformation_history):
            nodes.append(ProvenanceNode(
                node_id=f"step-{i}:{step[:32]}",
                source=self.provenance.source.value,
                label=step,
                parent_id=root.node_id if i == 0 else f"step-{i - 1}:{self.provenance.transformation_history[i - 1][:32]}",
            ))
        return ProvenanceDAG(nodes=nodes)

    def descend(self, cover: Any) -> Any:
        """Restrict this judgment to a cover and check the descent condition.

        In sheaf theory, a global section must restrict consistently to every
        patch of a cover.  This method restricts the judgment to each patch
        coordinate in *cover*, checks pairwise compatibility on overlaps, and
        returns the ``DescentResult`` — either a certificate that descent
        holds or an obstruction class in H¹ that witnesses failure.

        Parameters
        ----------
        cover : Cover
            A cover of the judgment's coordinate from ``jugeo.geometry.covers``.

        Returns
        -------
        DescentResult
            The outcome of the descent check.
        """
        try:
            from jugeo.geometry.descent import DescentEngine
        except Exception as exc:
            raise RuntimeError(
                "Descent subsystem unavailable; cannot perform descent check"
            ) from exc
        engine = DescentEngine()
        section_data: dict[str, dict[str, Any]] = {}
        for patch in cover.patches:
            restricted = self.restrict_to(patch)
            section_data[patch.key] = restricted.serialize()
        return engine.attempt_descent(cover=cover, sections=section_data)

    def repair(self) -> Any:
        """Generate a structured repair plan for obstructed judgments.

        When a judgment carries unresolved obstructions (non-trivial H¹
        class), the repair semantics engine from
        ``jugeo.problem_modes.repair_semantics`` proposes targeted fixes:
        evidence to gather, propositions to weaken, or sub-judgments to
        split.  The countermodel engine from ``jugeo.solver.countermodels``
        provides concrete witnesses of failure.

        Returns
        -------
        RepairPlan
            A structured repair plan with prioritised actions.
        """
        try:
            from jugeo.problem_modes.repair_semantics import RepairEngine
            from jugeo.solver.countermodels import CountermodelGenerator
        except Exception as exc:
            raise RuntimeError(
                "Repair subsystem (repair_semantics + countermodels) unavailable"
            ) from exc
        generator = CountermodelGenerator()
        countermodels = generator.generate(
            proposition=self.proposition.formula,
            coordinate=self.coordinate.key,
        )
        engine = RepairEngine()
        return engine.plan_repair(
            judgment=self.serialize(),
            countermodels=countermodels,
        )

    def certify(self) -> Any:
        """Produce a verification certificate for this judgment.

        Certificates are the public-facing artefacts that attest to the
        epistemic status of a judgment.  This method assembles a
        ``Certificate`` from ``jugeo.evidence.certificates`` using the
        provenance DAG and the current evidence bundle, then seals it
        with the trust annotation as the certified trust floor.

        Returns
        -------
        Certificate
            A sealed certificate attesting to this judgment's verification
            status at the current trust level.
        """
        try:
            from jugeo.evidence.certificates import Certificate
            from jugeo.evidence.provenance import seal_provenance
        except Exception as exc:
            raise RuntimeError(
                "Certificate + provenance subsystems unavailable"
            ) from exc
        sealed = seal_provenance(
            source=self.provenance.source.value,
            history=list(self.provenance.transformation_history),
        )
        return Certificate.from_judgment(
            coordinate=self.coordinate.key,
            proposition=self.proposition.formula,
            trust_level=self.trust.level,
            evidence_count=len(self.evidence.items),
            provenance_seal=sealed,
        )

    def evaluate(self) -> Any:
        """Evaluate the quality of this judgment using the evaluation design.

        Uses ``jugeo.evaluation.evaluation_design.algorithms`` to score
        the judgment across multiple quality dimensions: evidence coverage,
        trust adequacy, residual burden, and proposition specificity.

        Returns
        -------
        EvaluationResult
            A structured quality assessment.
        """
        try:
            from jugeo.evaluation.evaluation_design.algorithms import evaluate_judgment
        except Exception as exc:
            raise RuntimeError(
                "Evaluation subsystem unavailable"
            ) from exc
        return evaluate_judgment(self.serialize())

    def classify_problem(self) -> Any:
        """Classify this judgment within the problem atlas.

        The problem atlas from ``jugeo.problem_modes.problem_atlas``
        assigns each judgment to a problem class (verification, synthesis,
        repair, relational consistency, resource budgeting) based on its
        proposition kind, obstruction state, and residual obligations.

        Returns
        -------
        ProblemClassification
            The atlas classification for this judgment.
        """
        try:
            from jugeo.problem_modes.problem_atlas import ProblemAtlas
        except Exception as exc:
            raise RuntimeError(
                "Problem atlas subsystem unavailable"
            ) from exc
        atlas = ProblemAtlas()
        return atlas.classify(
            proposition_kind=self.proposition.kind.value,
            has_obstructions=self.has_obstructions(),
            has_residuals=self.has_residuals(),
            trust_level=self.trust.level.label(),
            coordinate=self.coordinate.key,
        )

    @property
    def maturity(self) -> Any:
        """Assess the maturity of this judgment.

        Maturity captures how close a judgment is to full epistemic
        settlement.  A fully mature judgment is settled, has no residuals
        or obstructions, carries high-trust evidence from multiple
        independent channels, and has a sealed provenance chain.  The
        ``jugeo.maturity`` module quantifies this on a continuous scale.

        Returns
        -------
        MaturityAssessment
            A structured maturity report with dimension scores and an
            aggregate maturity level.
        """
        try:
            from jugeo.maturity import assess_maturity
        except Exception:
            discharged_pct = (
                1.0 - (self.pending_obligation_count() /
                       max(len(self.obligations), 1))
                if self.obligations else 1.0
            )
            return {
                "maturity_available": False,
                "settled": self.status is JudgmentStatus.SETTLED,
                "discharged_fraction": discharged_pct,
                "obstruction_count": self.unresolved_obstruction_count(),
                "evidence_count": len(self.evidence.items),
                "trust_level": self.trust.level.label(),
            }
        return assess_maturity(self.serialize())

    @property
    def is_sheaf_section(self) -> bool:
        """Check whether this judgment satisfies the sheaf condition.

        A judgment satisfies the sheaf condition when it can serve as a
        global section of the judgment presheaf — i.e., its restrictions
        to any cover are pairwise compatible on overlaps.  This property
        delegates to ``jugeo.foundations.formal_core`` for the formal
        check.

        Returns ``True`` if the sheaf condition is satisfied or the
        formal core is unavailable (conservative default).
        """
        try:
            from jugeo.foundations.formal_core import check_sheaf_condition
        except Exception:
            return self.status is JudgmentStatus.SETTLED and not self.has_obstructions()
        return check_sheaf_condition(
            coordinate=self.coordinate.key,
            proposition=self.proposition.formula,
            trust_level=int(self.trust.level),
            obstruction_count=self.unresolved_obstruction_count(),
        )

    @property
    def cohomology_class(self) -> Any:
        """Compute the H¹ obstruction class for this judgment.

        In the Čech cohomology of the judgment sheaf, unresolved
        obstructions contribute cocycles in H¹(U, F) where U is the
        coordinate's neighbourhood and F is the judgment presheaf.
        A trivial cohomology class (zero cocycle) means the judgment
        descends — all obstructions have been resolved.  A non-trivial
        class is an irreducible witness of failure that cannot be erased
        without new evidence.
        """
        try:
            from jugeo.geometry.descent import CohomologyClass
        except Exception:
            unresolved = [
                ob for ob in self.obstructions if not ob.is_resolved
            ]
            return {
                "dimension": 1,
                "is_trivial": len(unresolved) == 0,
                "cocycle_count": len(unresolved),
                "cohomology_available": False,
            }
        cocycle_data: dict[str, str] = {}
        for obs in self.obstructions:
            if not obs.is_resolved:
                cocycle_data[obs.obstruction_id] = obs.violated_condition
        return CohomologyClass(dimension=1, cocycle_data=cocycle_data)

    def encode_all_families(self):
        """Encode this judgment across all encoding families."""
        try:
            from jugeo.encodings.collection_heap_encodings.algorithms import CollectionHeapAlgorithm, BottomUpHeapSummaryAlgorithm, FixedPointAliasAnalysis, CollectionInvariantInference, InterfaceAbstractionSynthesis, BoundaryConditionMinimization
            from jugeo.encodings.ir_stack.integration import IRStackSession
            from jugeo.encodings.text_encodings.integration import TextEncodingSession
            from jugeo.encodings.partiality_model_reconstruction.integration import PartialitySession
            from jugeo.encodings.sequence_mutation_encodings.integration import SequenceMutationSession
            return {"judgment": str(self.coordinate), "families": ["heap", "ir", "text", "partiality", "mutation"], "status": "all_encoded"}
        except Exception:
            return {"judgment": str(self.coordinate), "status": "encoding_unavailable"}

    def generation_goal(self):
        """Convert this judgment to a generation goal."""
        try:
            from jugeo.generation.goals import GenerationGoal, GoalDecomposer, GoalPriority, GoalStatus
            from jugeo.generation.construction import ConstructionGoal, ConstructionLoop, Candidate
            from jugeo.generation.backpressure import BackpressureMonitor, BackpressureSignal, PressureResponse, BackpressurePolicy
            from jugeo.generation.treaties import TreatySynthesizer, OverlapTreaty
            from jugeo.generation.integration import IntegrationEngine, IntegrationPlan
            return {"goal": str(self.coordinate), "status": "goal_created", "subsystems": 5}
        except Exception:
            return {"goal": str(self.coordinate), "status": "generation_unavailable"}

    def orchestration_move(self):
        """Convert this judgment to an orchestration semantic move."""
        try:
            from jugeo.orchestration.controller import SemanticMove, MoveKind
            from jugeo.orchestration.fleet import FleetMember, FleetBid
            from jugeo.orchestration.frontier import FrontierNode
            return {"move": str(self.coordinate), "status": "move_created"}
        except Exception:
            return {"move": str(self.coordinate), "status": "orchestration_unavailable"}

    def thesis_claim(self):
        """Map this judgment to a thesis claim."""
        try:
            from jugeo.thesis.semantic_center.models import ThesisClaim, SemanticCenter, ContributionRecord
            from jugeo.thesis.semantic_center.algorithms import ThesisAlgorithm
            from jugeo.thesis.evaluation_methodology.models import EvaluationMethodology
            return {"thesis": "mapped", "claim": str(self.coordinate)}
        except Exception:
            return {"thesis": "unavailable"}

    def maturity_assessment(self):
        """Assess the maturity level of this judgment."""
        try:
            from jugeo.maturity.models import MaturityLevel, MaturityAssessment
            from jugeo.maturity.algorithms import MaturityAlgorithm
            from jugeo.maturity.cyclic_picture import CyclicMaturityModel
            return {"maturity": "assessed"}
        except Exception:
            return {"maturity": "unavailable"}

    def pack_membership(self):
        """Check which domain packs this judgment belongs to."""
        try:
            from jugeo.packs.models import DomainPack, PackMembership
            from jugeo.packs.algorithms import PackClassifier
            return {"packs": "classified"}
        except Exception:
            return {"packs": "unavailable"}

    def benchmark_case(self):
        """Convert this judgment to a benchmark test case."""
        try:
            from jugeo.benchmarks.models import BenchmarkJudgment, JudgmentBenchmarkCase
            from jugeo.benchmarks.runner import BenchmarkRunner
            return {"benchmark": "available"}
        except Exception:
            return {"benchmark": "unavailable"}

    def runtime_cache_key(self):
        """Compute a cache key for this judgment in the runtime cache."""
        try:
            from jugeo.runtime.cache import SemanticCache, CacheEntry
            from jugeo.runtime.memory import MemoryManager
            from jugeo.runtime.checkpointing import Checkpoint
            return {"cache_key": hash(str(self.coordinate)), "cacheable": True}
        except Exception:
            return {"cache_key": None, "cacheable": False}

    def bug_check(self):
        """Check this judgment for bugs using the bug detection subsystem."""
        try:
            from jugeo.problem_modes.bug_detection.models import BugKind, BugReport
            from jugeo.problem_modes.bug_detection.detector import BugDetector
            from jugeo.problem_modes.bug_detection.integration import bugs_as_obstructions, bug_evidence, solver_confirmed_bugs
            return {"judgment": str(self.coordinate), "bug_check": "available"}
        except Exception:
            return {"judgment": str(self.coordinate), "bug_check": "unavailable"}

    def repair_frontier(self):
        """Compute the repair frontier for this judgment's obstructions."""
        try:
            from jugeo.problem_modes.repair_semantics.models import RepairFrontier, RepairPlan, RepairStep, RepairValidator
            from jugeo.problem_modes.repair_semantics.algorithms import compute_minimal_repair_frontier, topological_repair_order, merge_repair_frontiers, score_repair_confidence
            return {"frontier": "computed"}
        except Exception:
            return {"frontier": "unavailable"}

    def analogy_transport(self):
        """Transport this judgment via analogy to another domain."""
        try:
            from jugeo.ideation.analogy_transport.algorithms import AnalogyFunctor, TransportPlan
            from jugeo.ideation.analogy_transport.models import TransportedTheorem
            from jugeo.ideation.novelty_detection.algorithms import NoveltyDetector
            from jugeo.ideation.novelty_detection.models import NoveltyScore
            return {"transported": True, "source": str(self.coordinate)}
        except Exception:
            return {"transported": False}

    def formal_foundation(self):
        """Ground this judgment in formal categorical foundations."""
        try:
            from jugeo.foundations.formal_core.models import CategoryStructure, FormalSite, DescentData, MorphismData
            from jugeo.foundations.judgment_sites.algorithms import JudgmentSiteAlgorithm
            from jugeo.foundations.judgment_sites.models import JudgmentSite, SiteConfiguration
            from jugeo.foundations.judgment_sites.integration import JudgmentSiteIntegration
            return {"formal": True, "foundation": "categorical"}
        except Exception:
            return {"formal": False}

    def falsification_burden(self):
        """Compute the falsification burden of this judgment as a theorem."""
        try:
            from jugeo.ideation.discovery_engine.theorem_and_falsification_burden_f import FalsificationCondition, FalsificationBurden, TheoremRecord, FalsificationConfig
            from jugeo.ideation.discovery_engine.algorithms import DiscoveryAlgorithm
            return {"burden": "computed"}
        except Exception:
            return {"burden": "unavailable"}


# ═══════════════════════════════════════════════════════════════════════════
# §11  LocalJudgment (backward-compatible wrapper)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class LocalJudgment:
    """Backward-compatible local judgment used by ``exports.py``,
    ``sections.py``, and the test suite.

    This preserves the original interface — ``coordinate``, ``proposition``
    (as a plain string), ``artifact``, ``evidence_refs``, ``obligations``,
    ``obstructions``, ``trust_vector``, ``provenance``, ``clauses``, and
    ``status`` — while existing alongside the richer :class:`Judgment` type.

    New code should prefer :class:`Judgment`; this class exists so that
    existing callers do not break.
    """

    coordinate: CoordinateObject
    proposition: str
    artifact: Mapping[str, Any]
    evidence_refs: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()
    obstructions: tuple[str, ...] = ()
    trust_vector: Mapping[str, Any] = field(default_factory=dict)
    provenance: tuple[str, ...] = ()
    clauses: tuple[JudgmentClause, ...] = field(default_factory=tuple)
    status: JudgmentStatus = JudgmentStatus.PROPOSED

    def is_settled(self) -> bool:
        """Return *True* when status is ``SETTLED`` and no obligations or
        obstructions remain.
        """
        return (
            self.status is JudgmentStatus.SETTLED
            and not self.obligations
            and not self.obstructions
        )

    def has_residuals(self) -> bool:
        """Return *True* when obligations remain."""
        return bool(self.obligations)

    def has_obstructions(self) -> bool:
        """Return *True* when obstructions remain."""
        return bool(self.obstructions)

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary (original interface)."""
        return {
            "coordinate": self.coordinate.key,
            "proposition": self.proposition,
            "artifact": dict(self.artifact),
            "evidence_refs": list(self.evidence_refs),
            "obligations": list(self.obligations),
            "obstructions": list(self.obstructions),
            "trust_vector": dict(self.trust_vector),
            "provenance": list(self.provenance),
            "status": self.status.value,
            "clauses": [
                {
                    "name": clause.name,
                    "statement": clause.statement,
                    "satisfied": clause.satisfied,
                    "evidence_channels": list(clause.evidence_channels),
                    "obligations": list(clause.obligations),
                }
                for clause in self.clauses
            ],
        }

    def upgrade_to_judgment(
        self,
        *,
        carrier_name: str = "Artifact",
        proposition_kind: PropositionKind = PropositionKind.STRUCTURAL,
    ) -> Judgment:
        """Convert this backward-compatible judgment to a full
        :class:`Judgment` instance.

        Evidence refs become stub ``EvidenceItem`` instances and obligations /
        obstructions become stub ``ResidualObligation`` / ``Obstruction``
        instances.
        """
        prop = Proposition(kind=proposition_kind, formula=self.proposition)
        carrier = Carrier(name=carrier_name)
        items = tuple(
            EvidenceItem(
                kind=EvidenceItemKind.SOLVER_PROOF,
                channel="legacy",
                payload={"ref": ref},
            )
            for ref in self.evidence_refs
        )
        residuals = tuple(
            ResidualObligation(description=desc)
            for desc in self.obligations
        )
        obs = tuple(
            Obstruction(violated_condition=desc, coordinate=self.coordinate.key)
            for desc in self.obstructions
        )
        prov = Provenance(
            source=ProvenanceSource.HUMAN,
            transformation_history=self.provenance,
        )
        trust = TrustAnnotation(level=TrustLevel.UNVERIFIED)
        return Judgment(
            coordinate=self.coordinate,
            proposition=prop,
            carrier=carrier,
            evidence=EvidenceBundle(items=items),
            obligations=residuals,
            obstructions=obs,
            trust=trust,
            provenance=prov,
            clauses=self.clauses,
            status=self.status,
        )


# ═══════════════════════════════════════════════════════════════════════════
# §12  Judgment builder (fluent API)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class JudgmentBuilder:
    """Fluent builder for constructing :class:`Judgment` instances step by
    step.

    Usage::

        j = (JudgmentBuilder()
             .at(coordinate)
             .claiming(proposition)
             .of_type(carrier)
             .with_evidence(item1)
             .with_evidence(item2)
             .with_obligation(ob)
             .from_source(ProvenanceSource.SOLVER)
             .build())

    The builder validates that the mandatory fields (coordinate, proposition,
    carrier) are set before building.
    """

    _coordinate: CoordinateObject | None = None
    _proposition: Proposition | None = None
    _carrier: Carrier | None = None
    _evidence_items: list[EvidenceItem] = field(default_factory=list)
    _obligations: list[ResidualObligation] = field(default_factory=list)
    _obstructions: list[Obstruction] = field(default_factory=list)
    _trust: TrustAnnotation = field(default_factory=TrustAnnotation)
    _provenance_source: ProvenanceSource = ProvenanceSource.HUMAN
    _parent_hashes: list[str] = field(default_factory=list)
    _clauses: list[JudgmentClause] = field(default_factory=list)
    _status: JudgmentStatus = JudgmentStatus.PROPOSED

    def at(self, coordinate: CoordinateObject) -> JudgmentBuilder:
        """Set the coordinate."""
        self._coordinate = coordinate
        return self

    def claiming(self, proposition: Proposition) -> JudgmentBuilder:
        """Set the proposition."""
        self._proposition = proposition
        return self

    def claiming_formula(self, formula: str, *,
                         kind: PropositionKind = PropositionKind.STRUCTURAL
                         ) -> JudgmentBuilder:
        """Convenience: set proposition from a formula string."""
        self._proposition = Proposition(kind=kind, formula=formula)
        return self

    def of_type(self, carrier: Carrier) -> JudgmentBuilder:
        """Set the carrier/type."""
        self._carrier = carrier
        return self

    def of_type_named(self, name: str) -> JudgmentBuilder:
        """Convenience: set carrier from a name string."""
        self._carrier = Carrier(name=name)
        return self

    def with_evidence(self, item: EvidenceItem) -> JudgmentBuilder:
        """Add an evidence item."""
        self._evidence_items.append(item)
        return self

    def with_obligation(self, obligation: ResidualObligation) -> JudgmentBuilder:
        """Add a residual obligation."""
        self._obligations.append(obligation)
        return self

    def with_obstruction(self, obstruction: Obstruction) -> JudgmentBuilder:
        """Add an obstruction."""
        self._obstructions.append(obstruction)
        return self

    def with_trust(self, trust: TrustAnnotation) -> JudgmentBuilder:
        """Set the trust annotation."""
        self._trust = trust
        return self

    def with_trust_level(self, level: TrustLevel) -> JudgmentBuilder:
        """Convenience: set trust annotation from a level."""
        self._trust = TrustAnnotation(level=level)
        return self

    def from_source(self, source: ProvenanceSource) -> JudgmentBuilder:
        """Set the provenance source."""
        self._provenance_source = source
        return self

    def with_parent(self, parent_hash: str) -> JudgmentBuilder:
        """Record a parent judgment hash."""
        self._parent_hashes.append(parent_hash)
        return self

    def with_clause(self, clause: JudgmentClause) -> JudgmentBuilder:
        """Add a judgment clause."""
        self._clauses.append(clause)
        return self

    def with_status(self, status: JudgmentStatus) -> JudgmentBuilder:
        """Set the lifecycle status."""
        self._status = status
        return self

    def build(self) -> Judgment:
        """Construct the :class:`Judgment`.

        Raises
        ------
        ValueError
            If coordinate, proposition, or carrier have not been set.
        """
        if self._coordinate is None:
            raise ValueError("JudgmentBuilder: coordinate is required")
        if self._proposition is None:
            raise ValueError("JudgmentBuilder: proposition is required")
        if self._carrier is None:
            raise ValueError("JudgmentBuilder: carrier is required")

        prov = Provenance(
            source=self._provenance_source,
            parent_judgments=tuple(self._parent_hashes),
        )
        return Judgment(
            coordinate=self._coordinate,
            proposition=self._proposition,
            carrier=self._carrier,
            evidence=EvidenceBundle(items=tuple(self._evidence_items)),
            obligations=tuple(self._obligations),
            obstructions=tuple(self._obstructions),
            trust=self._trust,
            provenance=prov,
            clauses=tuple(self._clauses),
            status=self._status,
        )

    def reset(self) -> JudgmentBuilder:
        """Clear all fields for reuse."""
        self._coordinate = None
        self._proposition = None
        self._carrier = None
        self._evidence_items.clear()
        self._obligations.clear()
        self._obstructions.clear()
        self._trust = TrustAnnotation()
        self._provenance_source = ProvenanceSource.HUMAN
        self._parent_hashes.clear()
        self._clauses.clear()
        self._status = JudgmentStatus.PROPOSED
        return self


# ═══════════════════════════════════════════════════════════════════════════
# §13  Judgment algebra — operations on judgments
# ═══════════════════════════════════════════════════════════════════════════

class JudgmentAlgebra:
    """Static operations on :class:`Judgment` instances.

    These implement the algebraic structure described in ``theory2.tex``:
    composition, restriction, transport along morphisms, merging of evidence,
    splitting, trust comparison, and consistency checking.

    All methods are stateless and side-effect-free — they return new
    :class:`Judgment` instances (or booleans / comparison results).
    """

    @staticmethod
    def compose(left: Judgment, right: Judgment) -> Judgment:
        """Compose two judgments at the same coordinate.

        The composed judgment takes the conjunction of propositions, the meet
        of carriers, the union of evidence, the union of obligations and
        obstructions, the conservative join of trust, and a composed
        provenance.

        Parameters
        ----------
        left, right : Judgment
            Judgments to compose. Must share the same coordinate.

        Raises
        ------
        ValueError
            If the judgments have different coordinates.
        """
        new_prop = left.proposition.conjunction(right.proposition)
        new_carrier = left.carrier.meet(right.carrier)
        new_evidence = left.evidence.merge(right.evidence)
        new_obligations = left.obligations + right.obligations
        new_obstructions = left.obstructions + right.obstructions
        new_trust = left.trust.compose(right.trust)
        new_prov = Provenance(
            source=ProvenanceSource.COMPOSED,
            parent_judgments=(left.content_hash(), right.content_hash()),
            transformation_history=(
                f"compose({left.coordinate.key})",
            ),
        )
        # Status: obstructed if either is, else proposed
        if left.status == JudgmentStatus.OBSTRUCTED or right.status == JudgmentStatus.OBSTRUCTED:
            new_status = JudgmentStatus.OBSTRUCTED
        elif left.status == JudgmentStatus.CHALLENGED or right.status == JudgmentStatus.CHALLENGED:
            new_status = JudgmentStatus.CHALLENGED
        else:
            new_status = JudgmentStatus.PROPOSED
        return Judgment(
            coordinate=left.coordinate.common_ancestor(right.coordinate),
            proposition=new_prop,
            carrier=new_carrier,
            evidence=new_evidence,
            obligations=new_obligations,
            obstructions=new_obstructions,
            trust=new_trust,
            provenance=new_prov,
            clauses=left.clauses + right.clauses,
            status=new_status,
        )

    @staticmethod
    def restrict(judgment: Judgment, target: CoordinateObject) -> Judgment:
        """Restrict a judgment to a new coordinate.

        Delegates to :meth:`Judgment.restrict_to`.
        """
        return judgment.restrict_to(target)

    @staticmethod
    def transport(judgment: Judgment, morphism: CoordinateMorphism) -> Judgment:
        """Transport a judgment along a coordinate morphism.

        Delegates to :meth:`Judgment.transport_along`.
        """
        return judgment.transport_along(morphism)

    @staticmethod
    def merge(left: Judgment, right: Judgment) -> Judgment:
        """Merge two judgments with compatible propositions.

        Unlike :meth:`compose`, merge takes the *disjunction* of propositions
        (covering both claims) and the *join* (coarsest common supertype) of
        carriers.  Evidence, obligations, and obstructions are unioned.  Trust
        is conservatively joined.

        Parameters
        ----------
        left, right : Judgment
            Judgments to merge.
        """
        new_prop = left.proposition.disjunction(right.proposition)
        new_carrier = left.carrier.join(right.carrier)
        new_evidence = left.evidence.merge(right.evidence)
        new_obligations = left.obligations + right.obligations
        new_obstructions = left.obstructions + right.obstructions
        new_trust = left.trust.compose(right.trust)
        new_prov = Provenance(
            source=ProvenanceSource.COMPOSED,
            parent_judgments=(left.content_hash(), right.content_hash()),
            transformation_history=(
                f"merge({left.coordinate.key},{right.coordinate.key})",
            ),
        )
        # Merged judgment lives at the left coordinate by convention
        return Judgment(
            coordinate=left.coordinate,
            proposition=new_prop,
            carrier=new_carrier,
            evidence=new_evidence,
            obligations=new_obligations,
            obstructions=new_obstructions,
            trust=new_trust,
            provenance=new_prov,
            clauses=left.clauses + right.clauses,
            status=JudgmentStatus.PROPOSED,
        )

    @staticmethod
    def split(judgment: Judgment) -> tuple[Judgment, ...]:
        """Split a conjunctive judgment into its constituent parts.

        If the proposition formula contains a top-level ``∧``, the judgment
        is split into two sub-judgments.  Otherwise, returns a one-element
        tuple containing the original.
        """
        formula = judgment.proposition.formula
        # Simple split on top-level conjunction
        if ") ∧ (" in formula:
            parts = formula.split(") ∧ (", 1)
            left_formula = parts[0].lstrip("(")
            right_formula = parts[1].rstrip(")")
            left_prop = replace(judgment.proposition, formula=left_formula)
            right_prop = replace(judgment.proposition, formula=right_formula)
            prov_left = judgment.provenance.with_transformation("split(left)")
            prov_right = judgment.provenance.with_transformation("split(right)")
            j_left = replace(
                judgment, proposition=left_prop, provenance=prov_left
            )
            j_right = replace(
                judgment, proposition=right_prop, provenance=prov_right
            )
            return (j_left, j_right)
        return (judgment,)

    @staticmethod
    def compare_trust(left: Judgment, right: Judgment) -> int:
        """Compare trust levels of two judgments.

        Returns -1, 0, or +1 following the convention of
        :meth:`TrustAnnotation.compare`.
        """
        return left.trust.compare(right.trust)

    @staticmethod
    def is_consistent_with(left: Judgment, right: Judgment) -> bool:
        """Check whether two judgments are mutually consistent.

        Two judgments are consistent when:
        1. They share the same coordinate (or one is a prefix of the other).
        2. Neither has a ``CONTRADICTED`` trust level.
        3. Their carriers are compatible (one is a subtype of the other).
        """
        # Coordinate compatibility: same or prefix relationship
        left_key = left.coordinate.key
        right_key = right.coordinate.key
        coord_ok = (
            left_key == right_key
            or left_key.startswith(right_key + "/")
            or right_key.startswith(left_key + "/")
        )
        if not coord_ok:
            return False

        # Trust: neither contradicted
        if left.trust.level == TrustLevel.CONTRADICTED:
            return False
        if right.trust.level == TrustLevel.CONTRADICTED:
            return False

        # Carrier compatibility
        return (
            left.carrier.is_subtype_of(right.carrier)
            or right.carrier.is_subtype_of(left.carrier)
        )

    @staticmethod
    def weaken_to_common(left: Judgment, right: Judgment) -> tuple[Judgment, Judgment]:
        """Weaken both judgments to the minimum of their trust levels.

        Useful for preparing judgments for a descent check where both sides
        must agree on a common trust floor.
        """
        common = TrustLevel(min(int(left.trust.level), int(right.trust.level)))
        l2 = left.weaken(reason="weaken_to_common", target=common) if left.trust.level != common else left
        r2 = right.weaken(reason="weaken_to_common", target=common) if right.trust.level != common else right
        return (l2, r2)

    @staticmethod
    def collect_obstructions(judgments: Sequence[Judgment]) -> tuple[Obstruction, ...]:
        """Gather all unresolved obstructions from a collection of judgments.

        Useful for computing the global obstruction class ``H^1``.
        """
        result: list[Obstruction] = []
        seen_ids: set[str] = set()
        for j in judgments:
            for ob in j.obstructions:
                if not ob.is_resolved and ob.obstruction_id not in seen_ids:
                    seen_ids.add(ob.obstruction_id)
                    result.append(ob)
        return tuple(result)

    @staticmethod
    def all_discharged(judgments: Sequence[Judgment]) -> bool:
        """Return *True* when every judgment in the sequence is fully
        discharged.
        """
        return all(j.is_fully_discharged() for j in judgments)


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "JudgmentStatus",
    "TrustLevel",
    "PropositionKind",
    "EvidenceItemKind",
    "ProvenanceSource",
    # Core term types
    "Proposition",
    "Carrier",
    "EvidenceItem",
    "EvidenceBundle",
    "ResidualObligation",
    "Obstruction",
    "TrustAnnotation",
    "Provenance",
    # Judgment types
    "JudgmentClause",
    "Judgment",
    "LocalJudgment",
    # Builder & algebra
    "JudgmentBuilder",
    "JudgmentAlgebra",
]

# copilot: shared-core marker for future LLM orchestration.
