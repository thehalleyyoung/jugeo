r"""Evidence federation across JuGeo domain-pack boundaries.

The governing theory in ``preliminaries/theory2.tex`` defines federation as the
principled combination of evidence and judgments from multiple domain packs when
a claim spans pack boundaries.  The algebraic invariant that distinguishes this
from naïve merging is **kind preservation**: proof-backed evidence stays
proof-backed, solver-backed evidence stays solver-backed, and no transport step
silently promotes trust.

Formally, given packs
:math:`\mathfrak{P}_1, \ldots, \mathfrak{P}_n` and bridge theorems
:math:`\mathcal{B}_{ij}` connecting them, federation constructs a combined
evidence record

.. math::

    E_{\mathrm{fed}} = \bigoplus_{k=1}^{n} \varphi_k(E_k)

where each :math:`\varphi_k` is a kind-preserving adapter and :math:`\oplus`
respects the ordered trust algebra :math:`(\preceq, \oplus, \ominus)` from
theory2 §252.  The trust level of the result is the conservative meet across
contributors — never silently promoted above any individual contributor.

Design principles
-----------------

* **Kind preservation** — the evidence kind tag (proof, solver, oracle, …)
  survives every federation step.
* **No silent promotion** — combined trust ≤ min(contributor trusts) unless
  an explicit promotion certificate is supplied.
* **Jurisdiction compliance** — a pack may only contribute evidence for claims
  within its exported semantic kinds.
* **Bridge provenance** — every bridge traversal is recorded so the full
  federation path is auditable.

Public types
------------

:class:`FederationRequest`
    Immutable request envelope for a cross-pack federation.

:class:`FederationResult`
    Immutable result envelope including combined evidence and provenance.

:class:`FederationEngine`
    Main entry point: accepts a request and returns a result.

:class:`EvidenceCombiner`
    Combines evidence records from heterogeneous packs.

:class:`FederationPlan`
    Plans and optimises the federation route before execution.

:class:`FederationValidator`
    Post-hoc validation of federation results against theory2 invariants.

:class:`FederationCoordinator`
    Coordinates multi-pack negotiation, conflict resolution, and escalation.

:class:`FederationCache`
    TTL-aware cache for federation results keyed on proposition + coordinate.

:class:`FederationHistory`
    Append-only log of past federations for analytics and copilot summaries.

:class:`FederationDiagnostics`
    Human- and copilot-readable diagnostic reports.

:class:`FederationSerializer`
    Round-trip JSON serialization of requests, results, and plans.

Backward compatibility
----------------------

The original ``PackFederation`` helper is preserved as a legacy alias at the
bottom of the module.  New code should prefer the richer types above.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Iterable, Iterator, Mapping, Sequence

from jugeo.errors import FailureScope, raise_with_scope
from jugeo.packs.bridges import PackBridge
from jugeo.packs.catalog import (
    KNOWN_AUTHORITY_LEVELS,
    PackAdapter,
    PackBoundary,
    PackCatalog,
    PackDescriptor,
)

# ---------------------------------------------------------------------------
# Type aliases (consistent with catalog.py)
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
MutableJsonValue = JsonScalar | list["MutableJsonValue"] | dict[str, "MutableJsonValue"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FEDERATION_SPEC_PROVENANCE: Final[Mapping[str, JsonValue]] = MappingProxyType(
    {
        "source_tex": "preliminaries/theory2.tex",
        "chapter_title": "Domain packs, bridge theorems, and federation",
        "target_file": "src/jugeo/packs/federation.py",
        "target_test": "tests/jugeo/packs/test_federation.py",
        "stage": "shared-packs",
    }
)

_EMPTY_MAPPING: Final[Mapping[str, JsonValue]] = MappingProxyType({})

_DEFAULT_BUDGET: Final[float] = 60.0
_DEFAULT_TRUST_FLOOR: Final[str] = "quarantined"
_CACHE_DEFAULT_TTL: Final[float] = 300.0
_MAX_FEDERATION_DEPTH: Final[int] = 12


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FederationStatus(str, Enum):
    """Outcome status of a federation attempt.

    Mirrors the three-valued outcome model from theory2: full success when
    every required pack contributes, partial when at least one pack but not all
    contribute, and failed when no usable evidence could be assembled.
    """

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class EvidenceKindLabel(str, Enum):
    """Semantic kind labels for evidence records.

    These labels track the *origin* of evidence so that the federation
    combiner can enforce kind-preservation.  The ordered algebra on trust
    treats these as incomparable unless an explicit adapter is supplied.
    """

    PROOF = "proof"
    SOLVER = "solver"
    ORACLE = "oracle"
    RUNTIME = "runtime"
    HEURISTIC = "heuristic"
    COMPOSITE = "composite"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _authority_rank(authority: str | None) -> int:
    """Return the numeric rank of an authority level, or -1 if unknown."""
    if authority is None:
        return -1
    try:
        return KNOWN_AUTHORITY_LEVELS.index(authority)
    except ValueError:
        return -1


def _generate_request_id() -> str:
    """Generate a compact, collision-resistant request identifier."""
    return f"fed-{uuid.uuid4().hex[:12]}"


def _stable_hash(text: str) -> str:
    """Produce a deterministic 16-hex-char hash for cache keying."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _now() -> float:
    """Monotonic timestamp for internal timing; wall-clock for records."""
    return time.time()


def _freeze_json(value: Any) -> JsonValue:
    """Recursively freeze a JSON-like value into immutable form."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(k): _freeze_json(v) for k, v in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(f"Non-JSON value: {type(value).__name__}")


def _thaw_json(value: JsonValue) -> MutableJsonValue:
    """Recursively thaw an immutable JSON value into mutable form."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return {str(k): _thaw_json(v) for k, v in value.items()}


def _min_trust(levels: Sequence[str]) -> str:
    """Return the conservative (lowest) trust level across *levels*.

    This implements the theory2 meet rule: federated trust is never higher
    than any individual contributor's trust.
    """
    if not levels:
        return _DEFAULT_TRUST_FLOOR
    best_rank = min(_authority_rank(level) for level in levels)
    if best_rank < 0:
        return _DEFAULT_TRUST_FLOOR
    return KNOWN_AUTHORITY_LEVELS[best_rank]


# ---------------------------------------------------------------------------
# Data classes — FederationRequest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FederationRequest:
    """Immutable envelope describing a cross-pack federation request.

    A ``FederationRequest`` captures everything the :class:`FederationEngine`
    needs to plan, execute, and validate a federation: the proposition under
    evaluation, its semantic coordinate within the JuGeo site, the packs that
    *must* contribute evidence, the bridges available for transport, a wall-
    clock budget, and a minimum trust floor below which evidence is discarded.

    Attributes
    ----------
    request_id : str
        Unique identifier for this request (auto-generated if empty).
    proposition : str
        The claim or proposition to be evaluated across packs.
    coordinate : str
        Semantic coordinate in the JuGeo site locating *proposition*.
    required_packs : tuple[str, ...]
        Pack names that **must** contribute evidence for the result to be
        considered ``SUCCESS``.
    available_bridges : tuple[str, ...]
        Bridge theorem names available for evidence transport.
    budget : float
        Wall-clock seconds allowed for the federation attempt.
    trust_floor : str
        Minimum authority level; evidence below this is discarded.
    metadata : Mapping[str, JsonValue]
        Arbitrary extra fields for downstream consumers.
    """

    request_id: str = ""
    proposition: str = ""
    coordinate: str = ""
    required_packs: tuple[str, ...] = ()
    available_bridges: tuple[str, ...] = ()
    budget: float = _DEFAULT_BUDGET
    trust_floor: str = _DEFAULT_TRUST_FLOOR
    metadata: Mapping[str, JsonValue] = field(default_factory=lambda: _EMPTY_MAPPING)

    def __post_init__(self) -> None:
        """Normalise and validate fields after construction."""
        rid = self.request_id.strip() if self.request_id else _generate_request_id()
        object.__setattr__(self, "request_id", rid)
        object.__setattr__(self, "proposition", self.proposition.strip())
        object.__setattr__(self, "coordinate", self.coordinate.strip())
        object.__setattr__(
            self,
            "required_packs",
            tuple(dict.fromkeys(p.strip() for p in self.required_packs if p.strip())),
        )
        object.__setattr__(
            self,
            "available_bridges",
            tuple(dict.fromkeys(b.strip() for b in self.available_bridges if b.strip())),
        )
        if self.budget <= 0:
            object.__setattr__(self, "budget", _DEFAULT_BUDGET)
        if self.trust_floor not in KNOWN_AUTHORITY_LEVELS:
            object.__setattr__(self, "trust_floor", _DEFAULT_TRUST_FLOOR)

    def involves_pack(self, pack_name: str) -> bool:
        """Return True if *pack_name* is among the required contributors."""
        return pack_name in self.required_packs

    def involves_bridge(self, bridge_name: str) -> bool:
        """Return True if *bridge_name* is listed as available."""
        return bridge_name in self.available_bridges

    def trust_floor_rank(self) -> int:
        """Numeric rank of the trust floor for comparison."""
        return _authority_rank(self.trust_floor)

    def with_budget(self, new_budget: float) -> "FederationRequest":
        """Return a copy with an adjusted budget."""
        return FederationRequest(
            request_id=self.request_id,
            proposition=self.proposition,
            coordinate=self.coordinate,
            required_packs=self.required_packs,
            available_bridges=self.available_bridges,
            budget=new_budget,
            trust_floor=self.trust_floor,
            metadata=self.metadata,
        )

    def summary(self) -> str:
        """Human-readable one-line summary suitable for logs and copilot."""
        packs = ", ".join(self.required_packs) or "(none)"
        return (
            f"[{self.request_id}] proposition={self.proposition!r} "
            f"packs=[{packs}] budget={self.budget:.1f}s "
            f"floor={self.trust_floor}"
        )

    def to_dict(self) -> dict[str, MutableJsonValue]:
        """Serialise to a mutable JSON-compatible dictionary."""
        return {
            "request_id": self.request_id,
            "proposition": self.proposition,
            "coordinate": self.coordinate,
            "required_packs": list(self.required_packs),
            "available_bridges": list(self.available_bridges),
            "budget": self.budget,
            "trust_floor": self.trust_floor,
            "metadata": _thaw_json(_freeze_json(self.metadata)),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FederationRequest":
        """Construct from a JSON-compatible mapping."""
        return cls(
            request_id=str(payload.get("request_id", "")),
            proposition=str(payload.get("proposition", "")),
            coordinate=str(payload.get("coordinate", "")),
            required_packs=tuple(payload.get("required_packs", ())),
            available_bridges=tuple(payload.get("available_bridges", ())),
            budget=float(payload.get("budget", _DEFAULT_BUDGET)),
            trust_floor=str(payload.get("trust_floor", _DEFAULT_TRUST_FLOOR)),
            metadata=payload.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Data classes — FederationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FederationResult:
    """Immutable envelope for the outcome of a federation attempt.

    Attributes
    ----------
    request_id : str
        Ties this result back to the originating :class:`FederationRequest`.
    status : FederationStatus
        Overall outcome: ``SUCCESS``, ``PARTIAL``, or ``FAILED``.
    combined_evidence : Mapping[str, JsonValue]
        Merged evidence dictionary; empty on failure.
    trust_level : str
        Conservative (meet) trust across all contributors.
    contributing_packs : tuple[str, ...]
        Packs that actually contributed evidence.
    bridges_used : tuple[str, ...]
        Bridge theorems traversed during evidence transport.
    residuals : tuple[str, ...]
        Obligation identifiers left open after federation.
    provenance : Mapping[str, JsonValue]
        Full audit trail of the federation process.
    wall_seconds : float
        Elapsed wall-clock seconds for the federation.
    """

    request_id: str = ""
    status: FederationStatus = FederationStatus.FAILED
    combined_evidence: Mapping[str, JsonValue] = field(default_factory=lambda: _EMPTY_MAPPING)
    trust_level: str = _DEFAULT_TRUST_FLOOR
    contributing_packs: tuple[str, ...] = ()
    bridges_used: tuple[str, ...] = ()
    residuals: tuple[str, ...] = ()
    provenance: Mapping[str, JsonValue] = field(default_factory=lambda: _EMPTY_MAPPING)
    wall_seconds: float = 0.0

    # -- query helpers -------------------------------------------------------

    def succeeded(self) -> bool:
        """Return True if the federation fully succeeded."""
        return self.status is FederationStatus.SUCCESS

    def is_partial(self) -> bool:
        """Return True if at least some evidence was collected."""
        return self.status is FederationStatus.PARTIAL

    def failed(self) -> bool:
        """Return True when no usable evidence could be assembled."""
        return self.status is FederationStatus.FAILED

    def contributing_pack_count(self) -> int:
        """Number of packs that contributed."""
        return len(self.contributing_packs)

    def bridge_count(self) -> int:
        """Number of bridges traversed."""
        return len(self.bridges_used)

    def has_residuals(self) -> bool:
        """Return True if open obligations remain."""
        return len(self.residuals) > 0

    def trust_rank(self) -> int:
        """Numeric rank of the resulting trust level."""
        return _authority_rank(self.trust_level)

    def summary(self) -> str:
        """Compact human-readable summary for logs and copilot display."""
        packs = ", ".join(self.contributing_packs) or "(none)"
        return (
            f"[{self.request_id}] status={self.status.value} "
            f"trust={self.trust_level} packs=[{packs}] "
            f"bridges={self.bridge_count()} residuals={len(self.residuals)} "
            f"elapsed={self.wall_seconds:.3f}s"
        )

    def to_dict(self) -> dict[str, MutableJsonValue]:
        """Serialise to a mutable JSON-compatible dictionary."""
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "combined_evidence": _thaw_json(_freeze_json(self.combined_evidence)),
            "trust_level": self.trust_level,
            "contributing_packs": list(self.contributing_packs),
            "bridges_used": list(self.bridges_used),
            "residuals": list(self.residuals),
            "provenance": _thaw_json(_freeze_json(self.provenance)),
            "wall_seconds": self.wall_seconds,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FederationResult":
        """Reconstruct from a JSON-compatible mapping."""
        return cls(
            request_id=str(payload.get("request_id", "")),
            status=FederationStatus(payload.get("status", "failed")),
            combined_evidence=payload.get("combined_evidence", {}),
            trust_level=str(payload.get("trust_level", _DEFAULT_TRUST_FLOOR)),
            contributing_packs=tuple(payload.get("contributing_packs", ())),
            bridges_used=tuple(payload.get("bridges_used", ())),
            residuals=tuple(payload.get("residuals", ())),
            provenance=payload.get("provenance", {}),
            wall_seconds=float(payload.get("wall_seconds", 0.0)),
        )

    def copilot_summary(self) -> str:
        """Extended summary intended for copilot orchestration display.

        Includes residual obligations and provenance highlights so that an
        LLM orchestrator can decide whether to accept the result or retry.
        """
        lines = [self.summary()]
        if self.residuals:
            lines.append(f"  residuals: {', '.join(self.residuals)}")
        prov = self.provenance
        if prov:
            lines.append(f"  provenance keys: {', '.join(str(k) for k in prov)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# EvidenceCombiner
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EvidenceCombiner:
    r"""Combines evidence records from multiple packs into a single record.

    The combiner implements the :math:`\oplus` operator from theory2 §252.
    It distinguishes *homogeneous* merges (all evidence shares a kind) from
    *heterogeneous* merges (mixed kinds), applies conflict resolution, and
    ensures that no kind tag is lost or silently promoted during combination.

    Attributes
    ----------
    trust_floor : str
        Evidence with authority below this floor is discarded.
    allow_heterogeneous : bool
        If False, heterogeneous merges raise instead of wrapping as
        :pyattr:`EvidenceKindLabel.COMPOSITE`.
    """

    trust_floor: str = _DEFAULT_TRUST_FLOOR
    allow_heterogeneous: bool = True

    def combine(
        self,
        evidence_records: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, JsonValue]:
        """Combine a sequence of evidence mappings into one.

        Parameters
        ----------
        evidence_records : Sequence[Mapping[str, Any]]
            Each mapping must contain at least ``"kind"`` and ``"trust"``
            keys.  Additional keys are preserved verbatim.

        Returns
        -------
        Mapping[str, JsonValue]
            Frozen combined evidence record.

        Raises
        ------
        ValueError
            If *evidence_records* is empty or all records are below the
            trust floor.
        """
        filtered = self._filter_by_floor(evidence_records)
        if not filtered:
            raise_with_scope(
                "No evidence records above trust floor after filtering.",
                scope=FailureScope.PACK,
                code="federation-no-evidence",
            )
            raise AssertionError("unreachable")

        kinds = {str(rec.get("kind", "")) for rec in filtered}
        if len(kinds) == 1:
            return self.merge_homogeneous(filtered)
        return self.merge_heterogeneous(filtered)

    def merge_homogeneous(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, JsonValue]:
        """Merge evidence records that share the same kind label.

        The result inherits the shared kind and a trust level equal to the
        conservative meet of all contributors.

        Parameters
        ----------
        records : Sequence[Mapping[str, Any]]
            Homogeneous evidence records (same ``"kind"``).

        Returns
        -------
        Mapping[str, JsonValue]
            Frozen merged record.
        """
        kind = str(records[0].get("kind", EvidenceKindLabel.HEURISTIC.value))
        trust = self.compute_combined_trust(records)
        merged_details: list[MutableJsonValue] = []
        for rec in records:
            detail = dict(rec)
            detail.pop("kind", None)
            detail.pop("trust", None)
            merged_details.append(detail)
        result: dict[str, Any] = {
            "kind": kind,
            "trust": trust,
            "merge_type": "homogeneous",
            "contributor_count": len(records),
            "details": merged_details,
        }
        return _freeze_json(result)  # type: ignore[return-value]

    def merge_heterogeneous(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, JsonValue]:
        """Merge evidence records with differing kind labels.

        When heterogeneous merging is allowed, the result is tagged as
        ``COMPOSITE`` and preserves each contributor's original kind in a
        nested ``"components"`` list.  Kind-preservation is maintained within
        every component.

        Parameters
        ----------
        records : Sequence[Mapping[str, Any]]
            Evidence records with mixed ``"kind"`` values.

        Returns
        -------
        Mapping[str, JsonValue]
            Frozen composite evidence record.

        Raises
        ------
        ValueError
            If *allow_heterogeneous* is False.
        """
        if not self.allow_heterogeneous:
            raise_with_scope(
                "Heterogeneous evidence merge is disallowed by combiner policy.",
                scope=FailureScope.PACK,
                code="federation-heterogeneous-disallowed",
            )
            raise AssertionError("unreachable")

        trust = self.compute_combined_trust(records)
        components: list[dict[str, Any]] = []
        for rec in records:
            components.append({
                "kind": str(rec.get("kind", "")),
                "trust": str(rec.get("trust", self.trust_floor)),
                "payload": {k: v for k, v in rec.items() if k not in ("kind", "trust")},
            })
        result: dict[str, Any] = {
            "kind": EvidenceKindLabel.COMPOSITE.value,
            "trust": trust,
            "merge_type": "heterogeneous",
            "contributor_count": len(records),
            "components": components,
        }
        return _freeze_json(result)  # type: ignore[return-value]

    def resolve_conflicts(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any]]:
        """Resolve conflicting evidence records.

        Conflict resolution follows a conservative strategy: when two records
        cover the same sub-claim but disagree, the record with higher trust
        wins.  If trust is tied, both are preserved (the combined kind becomes
        ``COMPOSITE``).

        Parameters
        ----------
        records : Sequence[Mapping[str, Any]]
            Potentially conflicting evidence records.

        Returns
        -------
        Sequence[Mapping[str, Any]]
            Conflict-free sequence, possibly shorter than *records*.
        """
        if len(records) <= 1:
            return records

        by_claim: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for rec in records:
            claim_key = str(rec.get("claim", rec.get("proposition", "default")))
            by_claim[claim_key].append(rec)

        resolved: list[Mapping[str, Any]] = []
        for _claim, group in by_claim.items():
            if len(group) == 1:
                resolved.append(group[0])
                continue
            best_rank = max(_authority_rank(str(r.get("trust", ""))) for r in group)
            winners = [
                r for r in group
                if _authority_rank(str(r.get("trust", ""))) == best_rank
            ]
            resolved.extend(winners)
        return resolved

    def preserve_kinds(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any]]:
        """Validate that every record retains its original kind label.

        This is a **read-only check** — it returns the input unchanged but
        raises if any record lacks a ``"kind"`` key.

        Parameters
        ----------
        records : Sequence[Mapping[str, Any]]
            Evidence records to validate.

        Returns
        -------
        Sequence[Mapping[str, Any]]
            The same records, unchanged.

        Raises
        ------
        ValueError
            If any record is missing a ``"kind"`` key.
        """
        for idx, rec in enumerate(records):
            if "kind" not in rec:
                raise_with_scope(
                    f"Evidence record at index {idx} is missing required 'kind' key.",
                    scope=FailureScope.PACK,
                    code="federation-missing-kind",
                )
        return records

    def compute_combined_trust(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> str:
        """Compute the conservative meet of trust levels across *records*.

        Implements the theory2 rule that federated trust must never exceed the
        minimum contributor trust.

        Parameters
        ----------
        records : Sequence[Mapping[str, Any]]
            Evidence records each containing a ``"trust"`` key.

        Returns
        -------
        str
            The lowest authority level present.
        """
        levels = [str(rec.get("trust", self.trust_floor)) for rec in records]
        return _min_trust(levels)

    # -- private helpers -----------------------------------------------------

    def _filter_by_floor(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        """Discard records whose trust is below the configured floor."""
        floor_rank = _authority_rank(self.trust_floor)
        return [
            rec for rec in records
            if _authority_rank(str(rec.get("trust", ""))) >= floor_rank
        ]


# ---------------------------------------------------------------------------
# FederationPlan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FederationPlan:
    """An optimised plan for executing a federation request.

    The plan identifies which packs will contribute, which bridges are
    required for evidence transport, an estimated cost, and an optimised
    ordering.

    Attributes
    ----------
    request_id : str
        Ties the plan to its :class:`FederationRequest`.
    pack_contributions : tuple[str, ...]
        Ordered list of packs that will contribute.
    required_bridges : tuple[str, ...]
        Bridge theorems that must be traversed.
    estimated_cost : float
        Estimated wall-clock seconds for the full federation.
    steps : tuple[Mapping[str, JsonValue], ...]
        Ordered execution steps, each a frozen mapping.
    optimised : bool
        True if :meth:`optimize_plan` has been applied.
    """

    request_id: str = ""
    pack_contributions: tuple[str, ...] = ()
    required_bridges: tuple[str, ...] = ()
    estimated_cost: float = 0.0
    steps: tuple[Mapping[str, JsonValue], ...] = ()
    optimised: bool = False

    # -- planning API --------------------------------------------------------

    @classmethod
    def plan(
        cls,
        request: FederationRequest,
        catalog: PackCatalog,
        bridges: Sequence[PackBridge],
    ) -> "FederationPlan":
        """Build a federation plan for *request* against *catalog*.

        Parameters
        ----------
        request : FederationRequest
            The federation request to plan.
        catalog : PackCatalog
            Current pack catalog.
        bridges : Sequence[PackBridge]
            Available bridge theorems.

        Returns
        -------
        FederationPlan
            Un-optimised plan ready for :meth:`optimize_plan`.
        """
        contributions = cls._identify_pack_contributions(request, catalog)
        required = cls._identify_required_bridges(
            contributions, bridges, request.available_bridges,
        )
        cost = cls._estimate_cost(contributions, required)
        steps = cls._build_steps(contributions, required)
        return cls(
            request_id=request.request_id,
            pack_contributions=contributions,
            required_bridges=required,
            estimated_cost=cost,
            steps=steps,
            optimised=False,
        )

    def optimize_plan(self) -> "FederationPlan":
        """Return an optimised copy of this plan.

        Optimisation re-orders steps to minimise bridge traversals and
        groups packs that share bridges for parallel evaluation.

        Returns
        -------
        FederationPlan
            A new plan with *optimised* set to True.
        """
        if self.optimised or not self.steps:
            return self

        bridge_set = set(self.required_bridges)
        prioritised: list[Mapping[str, JsonValue]] = []
        deferred: list[Mapping[str, JsonValue]] = []

        for step in self.steps:
            step_bridges = step.get("bridges", ())
            if isinstance(step_bridges, (tuple, list)) and bridge_set.intersection(
                str(b) for b in step_bridges
            ):
                prioritised.append(step)
            else:
                deferred.append(step)

        ordered = tuple(prioritised + deferred)
        reduced_cost = self.estimated_cost * 0.85 if prioritised else self.estimated_cost
        return FederationPlan(
            request_id=self.request_id,
            pack_contributions=self.pack_contributions,
            required_bridges=self.required_bridges,
            estimated_cost=reduced_cost,
            steps=ordered,
            optimised=True,
        )

    def step_count(self) -> int:
        """Number of execution steps in the plan."""
        return len(self.steps)

    def involves_pack(self, pack_name: str) -> bool:
        """Check whether *pack_name* is scheduled to contribute."""
        return pack_name in self.pack_contributions

    def summary(self) -> str:
        """Human-readable summary for copilot and log display."""
        packs = ", ".join(self.pack_contributions) or "(none)"
        bridges = ", ".join(self.required_bridges) or "(none)"
        opt = " (optimised)" if self.optimised else ""
        return (
            f"[{self.request_id}] packs=[{packs}] bridges=[{bridges}] "
            f"steps={self.step_count()} cost={self.estimated_cost:.2f}s{opt}"
        )

    def to_dict(self) -> dict[str, MutableJsonValue]:
        """Serialise to a mutable JSON-compatible dictionary."""
        return {
            "request_id": self.request_id,
            "pack_contributions": list(self.pack_contributions),
            "required_bridges": list(self.required_bridges),
            "estimated_cost": self.estimated_cost,
            "steps": [_thaw_json(_freeze_json(dict(s))) for s in self.steps],
            "optimised": self.optimised,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FederationPlan":
        """Construct from a JSON-compatible mapping."""
        raw_steps = payload.get("steps", ())
        steps = tuple(_freeze_json(s) for s in raw_steps)  # type: ignore[arg-type]
        return cls(
            request_id=str(payload.get("request_id", "")),
            pack_contributions=tuple(payload.get("pack_contributions", ())),
            required_bridges=tuple(payload.get("required_bridges", ())),
            estimated_cost=float(payload.get("estimated_cost", 0.0)),
            steps=steps,  # type: ignore[arg-type]
            optimised=bool(payload.get("optimised", False)),
        )

    # -- private planning helpers -------------------------------------------

    @staticmethod
    def _identify_pack_contributions(
        request: FederationRequest,
        catalog: PackCatalog,
    ) -> tuple[str, ...]:
        """Determine which packs can contribute to *request*."""
        available = catalog.names()
        contributions: list[str] = []
        for pack in request.required_packs:
            if pack in available:
                contributions.append(pack)
        return tuple(contributions)

    @staticmethod
    def _identify_required_bridges(
        contributions: tuple[str, ...],
        bridges: Sequence[PackBridge],
        available_names: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Find bridges needed to connect contributing packs."""
        pack_set = set(contributions)
        needed: list[str] = []
        for bridge in bridges:
            if bridge.theorem_name in available_names:
                if bridge.source_pack in pack_set or bridge.target_pack in pack_set:
                    needed.append(bridge.theorem_name)
        return tuple(dict.fromkeys(needed))

    @staticmethod
    def _estimate_cost(
        contributions: tuple[str, ...],
        bridges: tuple[str, ...],
    ) -> float:
        """Rough wall-clock estimate: 1s per pack + 0.5s per bridge."""
        return float(len(contributions)) + 0.5 * float(len(bridges))

    @staticmethod
    def _build_steps(
        contributions: tuple[str, ...],
        bridges: tuple[str, ...],
    ) -> tuple[Mapping[str, JsonValue], ...]:
        """Build a linear sequence of execution steps."""
        steps: list[Mapping[str, JsonValue]] = []
        for pack in contributions:
            step: dict[str, Any] = {
                "action": "collect",
                "pack": pack,
                "bridges": [b for b in bridges],
            }
            steps.append(_freeze_json(step))  # type: ignore[arg-type]
        if bridges:
            transport: dict[str, Any] = {
                "action": "transport",
                "bridges": list(bridges),
            }
            steps.append(_freeze_json(transport))  # type: ignore[arg-type]
        combine: dict[str, Any] = {
            "action": "combine",
            "packs": list(contributions),
        }
        steps.append(_freeze_json(combine))  # type: ignore[arg-type]
        return tuple(steps)


# ---------------------------------------------------------------------------
# FederationValidator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FederationValidator:
    """Validates federation results against theory2 invariants.

    The validator enforces the four governing principles from the module
    docstring: kind preservation, no silent promotion, trust consistency,
    and jurisdiction compliance.

    Attributes
    ----------
    strict : bool
        If True, any violation raises immediately.  If False, violations
        are collected and returned as a tuple of issue strings.
    """

    strict: bool = False

    def validate(
        self,
        request: FederationRequest,
        result: FederationResult,
        catalog: PackCatalog,
    ) -> tuple[str, ...]:
        """Run all validation checks and return issue strings.

        Parameters
        ----------
        request : FederationRequest
            The originating request.
        result : FederationResult
            The federation result to validate.
        catalog : PackCatalog
            The pack catalog used during federation.

        Returns
        -------
        tuple[str, ...]
            Zero or more issue descriptions.  Empty means valid.
        """
        issues: list[str] = []
        issues.extend(self.check_kind_preservation(result))
        issues.extend(self.check_trust_consistency(request, result))
        issues.extend(self.check_no_silent_promotion(request, result))
        issues.extend(self.check_jurisdiction_compliance(request, result, catalog))
        issues.extend(self.check_bridge_provenance(result))

        if self.strict and issues:
            raise_with_scope(
                f"Federation validation failed: {'; '.join(issues)}",
                scope=FailureScope.PACK,
                code="federation-validation-failed",
                details={"issues": issues},
            )
        return tuple(issues)

    def check_kind_preservation(self, result: FederationResult) -> tuple[str, ...]:
        """Verify that evidence kind tags survive federation.

        Checks that the combined evidence retains a ``"kind"`` key and that
        any nested components also carry their original kind labels.

        Parameters
        ----------
        result : FederationResult
            The result to inspect.

        Returns
        -------
        tuple[str, ...]
            Issues found, if any.
        """
        issues: list[str] = []
        evidence = result.combined_evidence
        if not evidence:
            return ()
        if "kind" not in evidence:
            issues.append("combined-evidence-missing-kind")

        components = evidence.get("components", ())
        if isinstance(components, (tuple, list)):
            for idx, comp in enumerate(components):
                if isinstance(comp, Mapping) and "kind" not in comp:
                    issues.append(f"component-{idx}-missing-kind")
        return tuple(issues)

    def check_trust_consistency(
        self,
        request: FederationRequest,
        result: FederationResult,
    ) -> tuple[str, ...]:
        """Verify that the result trust is consistent with the request floor.

        The result trust must be ≥ the request's trust floor, and it must
        not exceed the minimum contributor trust (conservative meet rule).

        Parameters
        ----------
        request : FederationRequest
            Originating request with trust floor.
        result : FederationResult
            Result to check.

        Returns
        -------
        tuple[str, ...]
            Issues found, if any.
        """
        issues: list[str] = []
        result_rank = _authority_rank(result.trust_level)
        floor_rank = request.trust_floor_rank()

        if result_rank < floor_rank and result.succeeded():
            issues.append(
                f"trust-below-floor: result={result.trust_level} "
                f"floor={request.trust_floor}"
            )
        return tuple(issues)

    def check_no_silent_promotion(
        self,
        request: FederationRequest,
        result: FederationResult,
    ) -> tuple[str, ...]:
        """Enforce the no-silent-promotion invariant.

        The provenance must contain evidence of every trust transition.
        If the result trust exceeds any contributor's trust and no explicit
        promotion certificate is recorded, this is a violation.

        Parameters
        ----------
        request : FederationRequest
            Originating request.
        result : FederationResult
            Result to check.

        Returns
        -------
        tuple[str, ...]
            Issues found, if any.
        """
        issues: list[str] = []
        result_rank = _authority_rank(result.trust_level)

        provenance = result.provenance
        contributor_trusts = provenance.get("contributor_trusts", ())
        if isinstance(contributor_trusts, (tuple, list)):
            for entry in contributor_trusts:
                if isinstance(entry, Mapping):
                    pack_trust = str(entry.get("trust", _DEFAULT_TRUST_FLOOR))
                    if _authority_rank(pack_trust) < result_rank:
                        has_cert = bool(entry.get("promotion_certificate"))
                        if not has_cert:
                            issues.append(
                                f"silent-promotion: pack={entry.get('pack', '?')} "
                                f"trust={pack_trust} → result={result.trust_level}"
                            )
        return tuple(issues)

    def check_jurisdiction_compliance(
        self,
        request: FederationRequest,
        result: FederationResult,
        catalog: PackCatalog,
    ) -> tuple[str, ...]:
        """Verify that each pack only contributed within its jurisdiction.

        A pack's jurisdiction is defined by its ``exported_kinds``.  If the
        proposition's coordinate falls outside all exported kinds of a
        contributing pack, that pack has overstepped.

        Parameters
        ----------
        request : FederationRequest
            Originating request.
        result : FederationResult
            Result to check.
        catalog : PackCatalog
            Pack catalog with descriptors.

        Returns
        -------
        tuple[str, ...]
            Issues found, if any.
        """
        issues: list[str] = []
        coordinate = request.coordinate
        if not coordinate:
            return ()

        for pack_name in result.contributing_packs:
            descriptor = catalog.get(pack_name)
            if descriptor is None:
                issues.append(f"unknown-contributing-pack:{pack_name}")
                continue
            if descriptor.exported_kinds and coordinate not in descriptor.exported_kinds:
                issues.append(
                    f"jurisdiction-violation:{pack_name} "
                    f"coordinate={coordinate} not in exported_kinds"
                )
        return tuple(issues)

    def check_bridge_provenance(self, result: FederationResult) -> tuple[str, ...]:
        """Verify that every bridge used is recorded in provenance.

        Parameters
        ----------
        result : FederationResult
            The result to inspect.

        Returns
        -------
        tuple[str, ...]
            Issues found, if any.
        """
        issues: list[str] = []
        provenance = result.provenance
        recorded = set()
        bridge_log = provenance.get("bridge_log", ())
        if isinstance(bridge_log, (tuple, list)):
            for entry in bridge_log:
                if isinstance(entry, Mapping):
                    recorded.add(str(entry.get("bridge", "")))
                elif isinstance(entry, str):
                    recorded.add(entry)

        for bridge in result.bridges_used:
            if bridge not in recorded:
                issues.append(f"bridge-not-in-provenance:{bridge}")
        return tuple(issues)


# ---------------------------------------------------------------------------
# FederationEngine
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FederationEngine:
    """Main engine for executing cross-pack evidence federation.

    The engine accepts a :class:`FederationRequest`, plans the federation,
    routes evidence collection to the relevant packs, combines the results,
    validates invariants, and returns a :class:`FederationResult`.

    Attributes
    ----------
    catalog : PackCatalog
        The shared pack catalog.
    bridges : tuple[PackBridge, ...]
        Available bridge theorems.
    combiner : EvidenceCombiner
        Strategy for combining evidence from multiple sources.
    validator : FederationValidator
        Post-hoc validator for theory2 invariants.
    cache : FederationCache | None
        Optional result cache.
    """

    catalog: PackCatalog
    bridges: tuple[PackBridge, ...] = field(default_factory=tuple)
    combiner: EvidenceCombiner = field(default_factory=EvidenceCombiner)
    validator: FederationValidator = field(default_factory=FederationValidator)
    cache: "FederationCache | None" = None

    def federate(self, request: FederationRequest) -> FederationResult:
        """Execute a complete federation for *request*.

        This is the primary entry point.  It plans, executes, validates, and
        caches the result.

        Parameters
        ----------
        request : FederationRequest
            The federation request to execute.

        Returns
        -------
        FederationResult
            The federation outcome.
        """
        if self.cache is not None:
            cached = self.cache.get(request)
            if cached is not None:
                return cached

        start = _now()
        plan = FederationPlan.plan(request, self.catalog, list(self.bridges))
        plan = plan.optimize_plan()

        relevant = self.identify_relevant_packs(request)
        routed = self.route_to_packs(request, relevant)
        raw_evidence = self.collect_evidence(routed, request)
        bridges_used = self.apply_bridges(raw_evidence, plan)
        combined = self.combine_evidence(raw_evidence, request)
        trust = self.compute_trust(raw_evidence, request)
        consistency = self.check_consistency(combined, request)

        contributing = tuple(p for p, ev in routed.items() if ev)
        status = self._determine_status(request, contributing)
        elapsed = _now() - start

        provenance_dict: dict[str, Any] = {
            "plan_steps": plan.step_count(),
            "plan_optimised": plan.optimised,
            "consistency_issues": list(consistency),
            "contributor_trusts": [
                {"pack": p, "trust": str(routed[p].get("trust", _DEFAULT_TRUST_FLOOR))}
                for p in contributing
                if isinstance(routed.get(p), Mapping)
            ],
            "bridge_log": [
                {"bridge": b, "timestamp": _now()} for b in bridges_used
            ],
        }

        result = FederationResult(
            request_id=request.request_id,
            status=status,
            combined_evidence=combined,
            trust_level=trust,
            contributing_packs=contributing,
            bridges_used=bridges_used,
            residuals=tuple(consistency),
            provenance=_freeze_json(provenance_dict),  # type: ignore[arg-type]
            wall_seconds=elapsed,
        )

        self.validator.validate(request, result, self.catalog)

        if self.cache is not None:
            self.cache.put(request, result)

        return result

    def identify_relevant_packs(
        self,
        request: FederationRequest,
    ) -> tuple[str, ...]:
        """Determine which catalogued packs are relevant to *request*.

        A pack is relevant if it is in the request's required list **and**
        present in the catalog.

        Parameters
        ----------
        request : FederationRequest
            The request to evaluate.

        Returns
        -------
        tuple[str, ...]
            Relevant pack names.
        """
        available = set(self.catalog.names())
        return tuple(p for p in request.required_packs if p in available)

    def route_to_packs(
        self,
        request: FederationRequest,
        packs: tuple[str, ...],
    ) -> dict[str, Mapping[str, Any]]:
        """Route the request to each pack and collect stubs.

        In a full runtime this would invoke pack-local evaluation.  Here
        we construct stub evidence records using each pack's descriptor
        metadata.

        Parameters
        ----------
        request : FederationRequest
            The federation request.
        packs : tuple[str, ...]
            Packs to route to.

        Returns
        -------
        dict[str, Mapping[str, Any]]
            Mapping from pack name to its evidence stub.
        """
        results: dict[str, Mapping[str, Any]] = {}
        for pack_name in packs:
            descriptor = self.catalog.get(pack_name)
            if descriptor is None:
                continue
            evidence: dict[str, Any] = {
                "pack": pack_name,
                "proposition": request.proposition,
                "coordinate": request.coordinate,
                "kind": self._infer_evidence_kind(descriptor),
                "trust": descriptor.authority,
                "exported_kinds": list(descriptor.exported_kinds),
            }
            results[pack_name] = evidence
        return results

    def collect_evidence(
        self,
        routed: dict[str, Mapping[str, Any]],
        request: FederationRequest,
    ) -> list[Mapping[str, Any]]:
        """Flatten routed pack responses into a list of evidence records.

        Parameters
        ----------
        routed : dict[str, Mapping[str, Any]]
            Per-pack evidence mappings.
        request : FederationRequest
            The originating request (used for floor filtering).

        Returns
        -------
        list[Mapping[str, Any]]
            Evidence records above the trust floor.
        """
        floor_rank = request.trust_floor_rank()
        records: list[Mapping[str, Any]] = []
        for _pack, evidence in routed.items():
            trust_rank = _authority_rank(str(evidence.get("trust", "")))
            if trust_rank >= floor_rank:
                records.append(evidence)
        return records

    def combine_evidence(
        self,
        records: Sequence[Mapping[str, Any]],
        request: FederationRequest,
    ) -> Mapping[str, JsonValue]:
        """Delegate evidence combination to the configured combiner.

        Parameters
        ----------
        records : Sequence[Mapping[str, Any]]
            Evidence records to combine.
        request : FederationRequest
            Originating request.

        Returns
        -------
        Mapping[str, JsonValue]
            Combined evidence record.
        """
        if not records:
            return _EMPTY_MAPPING
        self.combiner.trust_floor = request.trust_floor
        try:
            return self.combiner.combine(records)
        except Exception:
            return _EMPTY_MAPPING

    def check_consistency(
        self,
        combined: Mapping[str, JsonValue],
        request: FederationRequest,
    ) -> tuple[str, ...]:
        """Check internal consistency of the combined evidence.

        Parameters
        ----------
        combined : Mapping[str, JsonValue]
            The combined evidence mapping.
        request : FederationRequest
            The originating request.

        Returns
        -------
        tuple[str, ...]
            Consistency issues found (used as residual obligations).
        """
        issues: list[str] = []
        if not combined:
            issues.append("empty-combined-evidence")
        if combined and "kind" not in combined:
            issues.append("combined-evidence-missing-kind")
        if combined and "trust" not in combined:
            issues.append("combined-evidence-missing-trust")
        return tuple(issues)

    def apply_bridges(
        self,
        records: Sequence[Mapping[str, Any]],
        plan: FederationPlan,
    ) -> tuple[str, ...]:
        """Determine which bridges were actually used during collection.

        Parameters
        ----------
        records : Sequence[Mapping[str, Any]]
            Collected evidence records.
        plan : FederationPlan
            The federation plan.

        Returns
        -------
        tuple[str, ...]
            Bridge theorem names that were traversed.
        """
        pack_names = {str(r.get("pack", "")) for r in records}
        used: list[str] = []
        for bridge in self.bridges:
            if bridge.theorem_name in plan.required_bridges:
                if bridge.source_pack in pack_names or bridge.target_pack in pack_names:
                    used.append(bridge.theorem_name)
        return tuple(dict.fromkeys(used))

    def compute_trust(
        self,
        records: Sequence[Mapping[str, Any]],
        request: FederationRequest,
    ) -> str:
        """Compute the federated trust level (conservative meet).

        Parameters
        ----------
        records : Sequence[Mapping[str, Any]]
            Evidence records.
        request : FederationRequest
            Originating request with trust floor.

        Returns
        -------
        str
            The lowest authority level across contributors, floored by the
            request's trust floor.
        """
        if not records:
            return request.trust_floor
        levels = [str(rec.get("trust", request.trust_floor)) for rec in records]
        meet = _min_trust(levels)
        if _authority_rank(meet) < request.trust_floor_rank():
            return request.trust_floor
        return meet

    # -- private helpers -----------------------------------------------------

    @staticmethod
    def _determine_status(
        request: FederationRequest,
        contributing: tuple[str, ...],
    ) -> FederationStatus:
        """Map the set of contributing packs to a status enum."""
        if not contributing:
            return FederationStatus.FAILED
        required = set(request.required_packs)
        if required and required.issubset(set(contributing)):
            return FederationStatus.SUCCESS
        return FederationStatus.PARTIAL

    @staticmethod
    def _infer_evidence_kind(descriptor: PackDescriptor) -> str:
        """Infer the evidence kind for a pack based on its capabilities."""
        caps = set(descriptor.capabilities)
        if "proof" in caps or "mechanically_verified" in caps:
            return EvidenceKindLabel.PROOF.value
        if "solver" in caps or "smt" in caps:
            return EvidenceKindLabel.SOLVER.value
        if "oracle" in caps:
            return EvidenceKindLabel.ORACLE.value
        if "runtime" in caps:
            return EvidenceKindLabel.RUNTIME.value
        return EvidenceKindLabel.HEURISTIC.value


# ---------------------------------------------------------------------------
# FederationCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FederationCoordinator:
    """Coordinates federation across multiple packs and engines.

    The coordinator handles multi-party negotiation: it asks each pack
    whether it *can* contribute, resolves conflicts when packs disagree,
    escalates failures that cannot be locally repaired, and provides a
    copilot-friendly coordination summary.

    Attributes
    ----------
    engine : FederationEngine
        The underlying federation engine.
    max_retries : int
        Maximum retry count per pack before escalation.
    escalation_threshold : int
        Number of pack failures before the entire federation is aborted.
    """

    engine: FederationEngine
    max_retries: int = 2
    escalation_threshold: int = 3

    def coordinate(self, request: FederationRequest) -> FederationResult:
        """Coordinate a federation end-to-end with retry and escalation.

        Parameters
        ----------
        request : FederationRequest
            The federation request.

        Returns
        -------
        FederationResult
            The final result after coordination.
        """
        contributions = self.negotiate_contributions(request)
        if not contributions:
            return FederationResult(
                request_id=request.request_id,
                status=FederationStatus.FAILED,
                trust_level=request.trust_floor,
                provenance=_freeze_json({"reason": "no-contributors"}),  # type: ignore[arg-type]
            )

        adjusted = FederationRequest(
            request_id=request.request_id,
            proposition=request.proposition,
            coordinate=request.coordinate,
            required_packs=contributions,
            available_bridges=request.available_bridges,
            budget=request.budget,
            trust_floor=request.trust_floor,
            metadata=request.metadata,
        )

        failures = 0
        last_result: FederationResult | None = None
        for attempt in range(self.max_retries + 1):
            result = self.engine.federate(adjusted)
            last_result = result
            if result.succeeded():
                return result
            conflicts = self.handle_conflicts(result)
            if not conflicts:
                return result
            failures += len(conflicts)
            if self.should_escalate(failures):
                return self.escalate_failures(request, result, conflicts)

        return last_result or FederationResult(
            request_id=request.request_id,
            status=FederationStatus.FAILED,
        )

    def negotiate_contributions(
        self,
        request: FederationRequest,
    ) -> tuple[str, ...]:
        """Ask each required pack whether it can contribute.

        A pack can contribute if it exists in the catalog and its authority
        meets or exceeds the request's trust floor.

        Parameters
        ----------
        request : FederationRequest
            The request to negotiate.

        Returns
        -------
        tuple[str, ...]
            Packs willing and able to contribute.
        """
        floor_rank = request.trust_floor_rank()
        willing: list[str] = []
        for pack_name in request.required_packs:
            descriptor = self.engine.catalog.get(pack_name)
            if descriptor is None:
                continue
            if _authority_rank(descriptor.authority) >= floor_rank:
                willing.append(pack_name)
        return tuple(willing)

    def handle_conflicts(self, result: FederationResult) -> tuple[str, ...]:
        """Identify residuals that represent inter-pack conflicts.

        Parameters
        ----------
        result : FederationResult
            The result to inspect.

        Returns
        -------
        tuple[str, ...]
            Conflict residual identifiers.
        """
        return tuple(
            r for r in result.residuals
            if "conflict" in r.lower() or "inconsisten" in r.lower()
        )

    def should_escalate(self, failure_count: int) -> bool:
        """Decide whether the failure count warrants escalation.

        Parameters
        ----------
        failure_count : int
            Cumulative pack failures.

        Returns
        -------
        bool
            True if escalation is warranted.
        """
        return failure_count >= self.escalation_threshold

    def escalate_failures(
        self,
        request: FederationRequest,
        result: FederationResult,
        conflicts: tuple[str, ...],
    ) -> FederationResult:
        """Escalate unresolvable conflicts into a FAILED result.

        Records the escalation reason in provenance for copilot display.

        Parameters
        ----------
        request : FederationRequest
            Originating request.
        result : FederationResult
            The partial result that triggered escalation.
        conflicts : tuple[str, ...]
            Conflict identifiers.

        Returns
        -------
        FederationResult
            A FAILED result with escalation provenance.
        """
        prov: dict[str, Any] = {
            "escalation": True,
            "conflicts": list(conflicts),
            "original_status": result.status.value,
            "original_contributing_packs": list(result.contributing_packs),
        }
        return FederationResult(
            request_id=request.request_id,
            status=FederationStatus.FAILED,
            combined_evidence=result.combined_evidence,
            trust_level=result.trust_level,
            contributing_packs=result.contributing_packs,
            bridges_used=result.bridges_used,
            residuals=result.residuals + conflicts,
            provenance=_freeze_json(prov),  # type: ignore[arg-type]
            wall_seconds=result.wall_seconds,
        )

    def copilot_coordinate(self, request: FederationRequest) -> str:
        """Run coordination and return a copilot-friendly summary.

        Intended for LLM orchestration layers that need a textual
        report rather than a structured result.

        Parameters
        ----------
        request : FederationRequest
            The federation request.

        Returns
        -------
        str
            Multi-line summary suitable for copilot display.
        """
        result = self.coordinate(request)
        lines = [
            "# Federation Coordination Report",
            "",
            f"Request: {request.request_id}",
            f"Status: {result.status.value}",
            f"Trust: {result.trust_level}",
            f"Contributing packs: {', '.join(result.contributing_packs) or '(none)'}",
            f"Bridges used: {', '.join(result.bridges_used) or '(none)'}",
            f"Elapsed: {result.wall_seconds:.3f}s",
        ]
        if result.residuals:
            lines.append(f"Residuals: {', '.join(result.residuals)}")
        # copilot: include a recommendation for the orchestration layer
        if result.succeeded():
            lines.append("\nRecommendation: Accept federated evidence.")
        elif result.is_partial():
            lines.append("\nRecommendation: Review partial evidence before accepting.")
        else:
            lines.append("\nRecommendation: Federation failed; consider retrying "
                         "with relaxed trust floor or fewer required packs.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# FederationCache
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FederationCache:
    """TTL-aware cache for federation results.

    Keys are derived from the proposition and coordinate of the request.
    The cache supports per-pack invalidation so that when a pack's catalog
    entry changes, all affected federation results are purged.

    Attributes
    ----------
    ttl : float
        Time-to-live in seconds for cache entries.
    max_size : int
        Maximum number of cached entries.
    """

    ttl: float = _CACHE_DEFAULT_TTL
    max_size: int = 256
    _store: dict[str, tuple[float, FederationResult]] = field(
        default_factory=dict, repr=False,
    )
    _hits: int = field(default=0, repr=False)
    _misses: int = field(default=0, repr=False)

    def get(self, request: FederationRequest) -> FederationResult | None:
        """Retrieve a cached result for *request*, or None on miss.

        Expired entries are evicted lazily on access.

        Parameters
        ----------
        request : FederationRequest
            The request to look up.

        Returns
        -------
        FederationResult | None
            Cached result if valid, else None.
        """
        key = self._cache_key(request)
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        ts, result = entry
        if (_now() - ts) > self.ttl:
            del self._store[key]
            self._misses += 1
            return None
        self._hits += 1
        return result

    def put(self, request: FederationRequest, result: FederationResult) -> None:
        """Store a federation result in the cache.

        If the cache exceeds ``max_size``, the oldest entry is evicted.

        Parameters
        ----------
        request : FederationRequest
            The originating request (used to derive the cache key).
        result : FederationResult
            The result to cache.
        """
        if len(self._store) >= self.max_size:
            self._evict_oldest()
        key = self._cache_key(request)
        self._store[key] = (_now(), result)

    def invalidate(self, request: FederationRequest) -> bool:
        """Remove a specific entry from the cache.

        Parameters
        ----------
        request : FederationRequest
            The request whose cached result should be removed.

        Returns
        -------
        bool
            True if an entry was removed.
        """
        key = self._cache_key(request)
        if key in self._store:
            del self._store[key]
            return True
        return False

    def invalidate_for_pack(self, pack_name: str) -> int:
        """Remove all cached results that involved *pack_name*.

        Parameters
        ----------
        pack_name : str
            The pack whose cached federations should be purged.

        Returns
        -------
        int
            Number of entries removed.
        """
        to_remove: list[str] = []
        for key, (_ts, result) in self._store.items():
            if pack_name in result.contributing_packs:
                to_remove.append(key)
        for key in to_remove:
            del self._store[key]
        return len(to_remove)

    def clear(self) -> int:
        """Remove all cached entries.

        Returns
        -------
        int
            Number of entries removed.
        """
        count = len(self._store)
        self._store.clear()
        return count

    def hit_rate(self) -> float:
        """Return the cache hit rate as a float in [0.0, 1.0].

        Returns
        -------
        float
            Hit rate, or 0.0 if no lookups have been performed.
        """
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return self._hits / total

    def statistics(self) -> dict[str, Any]:
        """Return diagnostic statistics about cache usage.

        Returns
        -------
        dict[str, Any]
            Dictionary with hits, misses, size, hit_rate, and ttl.
        """
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._store),
            "max_size": self.max_size,
            "hit_rate": self.hit_rate(),
            "ttl": self.ttl,
        }

    # -- private helpers -----------------------------------------------------

    @staticmethod
    def _cache_key(request: FederationRequest) -> str:
        """Derive a deterministic cache key from a request."""
        raw = f"{request.proposition}|{request.coordinate}|{','.join(request.required_packs)}"
        return _stable_hash(raw)

    def _evict_oldest(self) -> None:
        """Remove the oldest entry by timestamp."""
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k][0])
        del self._store[oldest_key]


# ---------------------------------------------------------------------------
# FederationHistory
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FederationHistory:
    """Append-only log of federation attempts for analytics.

    Provides query methods for inspecting past federations by proposition,
    by pack combination, and for computing aggregate success rates.

    Attributes
    ----------
    max_entries : int
        Maximum entries before the oldest are dropped.
    """

    max_entries: int = 1000
    _entries: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def record(
        self,
        request: FederationRequest,
        result: FederationResult,
    ) -> None:
        """Append a federation attempt to the history.

        Parameters
        ----------
        request : FederationRequest
            The request that was executed.
        result : FederationResult
            The result produced.
        """
        entry: dict[str, Any] = {
            "request_id": request.request_id,
            "proposition": request.proposition,
            "coordinate": request.coordinate,
            "required_packs": list(request.required_packs),
            "status": result.status.value,
            "trust_level": result.trust_level,
            "contributing_packs": list(result.contributing_packs),
            "bridges_used": list(result.bridges_used),
            "wall_seconds": result.wall_seconds,
            "timestamp": _now(),
        }
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

    def by_proposition(self, proposition: str) -> tuple[dict[str, Any], ...]:
        """Return all history entries matching *proposition*.

        Parameters
        ----------
        proposition : str
            The proposition string to filter on.

        Returns
        -------
        tuple[dict[str, Any], ...]
            Matching entries in chronological order.
        """
        return tuple(
            e for e in self._entries if e.get("proposition") == proposition
        )

    def by_pack_combination(
        self,
        packs: Iterable[str],
    ) -> tuple[dict[str, Any], ...]:
        """Return entries where the contributing packs match *packs* exactly.

        Parameters
        ----------
        packs : Iterable[str]
            Pack names to match.

        Returns
        -------
        tuple[dict[str, Any], ...]
            Matching entries.
        """
        target = set(packs)
        return tuple(
            e for e in self._entries
            if set(e.get("contributing_packs", [])) == target
        )

    def success_rate(self) -> float:
        """Compute the overall success rate of recorded federations.

        Returns
        -------
        float
            Success rate in [0.0, 1.0], or 0.0 if no entries.
        """
        if not self._entries:
            return 0.0
        successes = sum(
            1 for e in self._entries if e.get("status") == FederationStatus.SUCCESS.value
        )
        return successes / len(self._entries)

    def common_bridge_paths(self, top_n: int = 5) -> tuple[tuple[str, int], ...]:
        """Return the most frequently used bridge combinations.

        Parameters
        ----------
        top_n : int
            Number of top combinations to return.

        Returns
        -------
        tuple[tuple[str, int], ...]
            Pairs of (bridge_path_key, count) sorted by descending count.
        """
        counter: dict[str, int] = defaultdict(int)
        for entry in self._entries:
            bridges = entry.get("bridges_used", [])
            key = "|".join(sorted(bridges)) if bridges else "(none)"
            counter[key] += 1
        ranked = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)
        return tuple(ranked[:top_n])

    def average_wall_seconds(self) -> float:
        """Compute the mean wall-clock time across all entries.

        Returns
        -------
        float
            Average seconds, or 0.0 if no entries.
        """
        if not self._entries:
            return 0.0
        total = sum(e.get("wall_seconds", 0.0) for e in self._entries)
        return total / len(self._entries)

    def copilot_federation_summary(self) -> str:
        """Generate a copilot-friendly summary of federation history.

        Intended for LLM orchestration layers that need a high-level
        overview of how federations have been performing.

        Returns
        -------
        str
            Multi-line human-readable summary.
        """
        total = len(self._entries)
        if total == 0:
            return "No federation history recorded yet."

        lines = [
            "# Federation History Summary",
            "",
            f"Total federations: {total}",
            f"Success rate: {self.success_rate():.1%}",
            f"Average wall time: {self.average_wall_seconds():.3f}s",
            "",
            "Top bridge paths:",
        ]
        for path, count in self.common_bridge_paths():
            lines.append(f"  {path}: {count} uses")

        # copilot: include trend information when enough data is available
        if total >= 10:
            recent = self._entries[-10:]
            recent_success = sum(
                1 for e in recent
                if e.get("status") == FederationStatus.SUCCESS.value
            )
            lines.append(f"\nRecent trend (last 10): {recent_success}/10 successes")

        return "\n".join(lines)

    def entry_count(self) -> int:
        """Return the total number of recorded entries.

        Returns
        -------
        int
            Number of history entries.
        """
        return len(self._entries)


# ---------------------------------------------------------------------------
# FederationDiagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FederationDiagnostics:
    """Diagnostic reporting for federation operations.

    Produces human- and copilot-readable reports analysing federation
    results, pack contributions, bridge usage, and trust behaviour.

    Attributes
    ----------
    history : FederationHistory
        History log to draw analytics from.
    catalog : PackCatalog
        Pack catalog for descriptor lookups.
    """

    history: FederationHistory
    catalog: PackCatalog

    def federation_summary(self) -> str:
        """Generate an overall federation health summary.

        Returns
        -------
        str
            Multi-line diagnostic report.
        """
        total = self.history.entry_count()
        rate = self.history.success_rate()
        avg = self.history.average_wall_seconds()
        lines = [
            "=== Federation Diagnostics ===",
            f"Total attempts: {total}",
            f"Success rate:   {rate:.1%}",
            f"Avg wall time:  {avg:.3f}s",
            f"Catalog packs:  {len(self.catalog)}",
        ]
        return "\n".join(lines)

    def pack_contribution_report(self) -> str:
        """Report how often each pack contributes to federations.

        Returns
        -------
        str
            Tabular report of pack contribution counts.
        """
        counter: dict[str, int] = defaultdict(int)
        for entry in self.history._entries:
            for pack in entry.get("contributing_packs", []):
                counter[pack] += 1

        lines = ["=== Pack Contribution Report ==="]
        if not counter:
            lines.append("(no contributions recorded)")
            return "\n".join(lines)

        for pack, count in sorted(counter.items(), key=lambda kv: kv[1], reverse=True):
            descriptor = self.catalog.get(pack)
            authority = descriptor.authority if descriptor else "unknown"
            lines.append(f"  {pack}: {count} contributions (authority={authority})")
        return "\n".join(lines)

    def bridge_usage_report(self) -> str:
        """Report how often each bridge is used in federations.

        Returns
        -------
        str
            Tabular report of bridge usage counts.
        """
        counter: dict[str, int] = defaultdict(int)
        for entry in self.history._entries:
            for bridge in entry.get("bridges_used", []):
                counter[bridge] += 1

        lines = ["=== Bridge Usage Report ==="]
        if not counter:
            lines.append("(no bridge usage recorded)")
            return "\n".join(lines)

        for bridge, count in sorted(counter.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"  {bridge}: {count} traversals")
        return "\n".join(lines)

    def trust_analysis(self) -> str:
        """Analyse trust-level distribution across federation results.

        Returns
        -------
        str
            Report of trust level frequencies and any anomalies.
        """
        counter: dict[str, int] = defaultdict(int)
        for entry in self.history._entries:
            trust = entry.get("trust_level", _DEFAULT_TRUST_FLOOR)
            counter[trust] += 1

        lines = ["=== Trust Analysis ==="]
        if not counter:
            lines.append("(no federations recorded)")
            return "\n".join(lines)

        for level in KNOWN_AUTHORITY_LEVELS:
            count = counter.get(level, 0)
            bar = "#" * min(count, 40)
            lines.append(f"  {level:20s}: {count:4d} {bar}")

        unknown = {k: v for k, v in counter.items() if k not in KNOWN_AUTHORITY_LEVELS}
        if unknown:
            lines.append("  --- unknown levels ---")
            for level, count in unknown.items():
                lines.append(f"  {level:20s}: {count:4d}")

        # copilot: flag potential trust anomalies
        quarantined_count = counter.get("quarantined", 0)
        total = sum(counter.values())
        if total > 0 and quarantined_count / total > 0.5:
            lines.append(
                "\n⚠ Warning: >50% of federations result in 'quarantined' trust. "
                "Review pack authority levels or trust floor settings."
            )
        return "\n".join(lines)

    def copilot_diagnostic_report(self) -> str:
        """Full diagnostic report formatted for copilot consumption.

        Returns
        -------
        str
            Concatenation of all sub-reports.
        """
        sections = [
            self.federation_summary(),
            "",
            self.pack_contribution_report(),
            "",
            self.bridge_usage_report(),
            "",
            self.trust_analysis(),
        ]
        return "\n".join(sections)


# ---------------------------------------------------------------------------
# FederationSerializer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FederationSerializer:
    """JSON round-trip serialization for federation types.

    Provides ``serialize_*`` and ``deserialize_*`` methods for
    :class:`FederationRequest`, :class:`FederationResult`, and
    :class:`FederationPlan`.

    Attributes
    ----------
    indent : int | None
        JSON indentation level.  None for compact output.
    sort_keys : bool
        Whether to sort JSON keys for deterministic output.
    """

    indent: int | None = 2
    sort_keys: bool = True

    def serialize_request(self, request: FederationRequest) -> str:
        """Serialize a :class:`FederationRequest` to JSON.

        Parameters
        ----------
        request : FederationRequest
            The request to serialize.

        Returns
        -------
        str
            JSON string.
        """
        return json.dumps(
            request.to_dict(),
            indent=self.indent,
            sort_keys=self.sort_keys,
        )

    def deserialize_request(self, text: str) -> FederationRequest:
        """Deserialize a :class:`FederationRequest` from JSON.

        Parameters
        ----------
        text : str
            JSON string.

        Returns
        -------
        FederationRequest
            Reconstructed request.

        Raises
        ------
        json.JSONDecodeError
            If *text* is not valid JSON.
        """
        payload = json.loads(text)
        return FederationRequest.from_dict(payload)

    def serialize_result(self, result: FederationResult) -> str:
        """Serialize a :class:`FederationResult` to JSON.

        Parameters
        ----------
        result : FederationResult
            The result to serialize.

        Returns
        -------
        str
            JSON string.
        """
        return json.dumps(
            result.to_dict(),
            indent=self.indent,
            sort_keys=self.sort_keys,
        )

    def deserialize_result(self, text: str) -> FederationResult:
        """Deserialize a :class:`FederationResult` from JSON.

        Parameters
        ----------
        text : str
            JSON string.

        Returns
        -------
        FederationResult
            Reconstructed result.
        """
        payload = json.loads(text)
        return FederationResult.from_dict(payload)

    def serialize_plan(self, plan: FederationPlan) -> str:
        """Serialize a :class:`FederationPlan` to JSON.

        Parameters
        ----------
        plan : FederationPlan
            The plan to serialize.

        Returns
        -------
        str
            JSON string.
        """
        return json.dumps(
            plan.to_dict(),
            indent=self.indent,
            sort_keys=self.sort_keys,
        )

    def deserialize_plan(self, text: str) -> FederationPlan:
        """Deserialize a :class:`FederationPlan` from JSON.

        Parameters
        ----------
        text : str
            JSON string.

        Returns
        -------
        FederationPlan
            Reconstructed plan.
        """
        payload = json.loads(text)
        return FederationPlan.from_dict(payload)

    def serialize_batch(
        self,
        items: Sequence[FederationRequest | FederationResult | FederationPlan],
    ) -> str:
        """Serialize a mixed batch of federation objects to a JSON array.

        Each item is wrapped in an envelope with a ``"type"`` discriminator
        so that :meth:`deserialize_batch` can reconstruct the correct types.

        Parameters
        ----------
        items : Sequence[FederationRequest | FederationResult | FederationPlan]
            Objects to serialize.

        Returns
        -------
        str
            JSON array string.
        """
        envelopes: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, FederationRequest):
                envelopes.append({"type": "request", "data": item.to_dict()})
            elif isinstance(item, FederationResult):
                envelopes.append({"type": "result", "data": item.to_dict()})
            elif isinstance(item, FederationPlan):
                envelopes.append({"type": "plan", "data": item.to_dict()})
            else:
                raise TypeError(f"Unsupported type: {type(item).__name__}")
        return json.dumps(
            envelopes,
            indent=self.indent,
            sort_keys=self.sort_keys,
        )

    def deserialize_batch(
        self,
        text: str,
    ) -> tuple[FederationRequest | FederationResult | FederationPlan, ...]:
        """Deserialize a batch of federation objects from a JSON array.

        Parameters
        ----------
        text : str
            JSON array string produced by :meth:`serialize_batch`.

        Returns
        -------
        tuple[FederationRequest | FederationResult | FederationPlan, ...]
            Reconstructed objects.

        Raises
        ------
        ValueError
            If an envelope has an unknown ``"type"`` discriminator.
        """
        envelopes = json.loads(text)
        items: list[FederationRequest | FederationResult | FederationPlan] = []
        for env in envelopes:
            kind = env.get("type", "")
            data = env.get("data", {})
            if kind == "request":
                items.append(FederationRequest.from_dict(data))
            elif kind == "result":
                items.append(FederationResult.from_dict(data))
            elif kind == "plan":
                items.append(FederationPlan.from_dict(data))
            else:
                raise ValueError(f"Unknown federation object type: {kind!r}")
        return tuple(items)

    def round_trip_request(self, request: FederationRequest) -> FederationRequest:
        """Serialize and immediately deserialize a request (integrity check).

        Parameters
        ----------
        request : FederationRequest
            Request to round-trip.

        Returns
        -------
        FederationRequest
            Reconstructed request.
        """
        return self.deserialize_request(self.serialize_request(request))

    def round_trip_result(self, result: FederationResult) -> FederationResult:
        """Serialize and immediately deserialize a result (integrity check).

        Parameters
        ----------
        result : FederationResult
            Result to round-trip.

        Returns
        -------
        FederationResult
            Reconstructed result.
        """
        return self.deserialize_result(self.serialize_result(result))


# ---------------------------------------------------------------------------
# Legacy compatibility — PackFederation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PackFederation:
    """Legacy federation helper for shared pack registries.

    Retained for backward compatibility with code that predates the richer
    federation types.  New code should prefer :class:`FederationEngine`.

    Attributes
    ----------
    catalogs : tuple[PackCatalog, ...]
        Pack catalogs to federate.
    bridges : tuple[PackBridge, ...]
        Available bridge theorems.
    """

    catalogs: tuple[PackCatalog, ...]
    bridges: tuple[PackBridge, ...] = field(default_factory=tuple)

    def merged_catalog(self) -> PackCatalog:
        """Merge all catalogs into a single unified catalog.

        Returns
        -------
        PackCatalog
            A new catalog containing all descriptors from all sources.
        """
        merged = PackCatalog()
        for catalog in self.catalogs:
            for descriptor in catalog.list_descriptors():
                merged.register(descriptor)
        return merged

    def reachable_packs(self, source_pack: str) -> tuple[str, ...]:
        """Return packs reachable from *source_pack* via bridges.

        Parameters
        ----------
        source_pack : str
            The starting pack.

        Returns
        -------
        tuple[str, ...]
            Sorted names of reachable target packs.
        """
        return tuple(sorted(
            {bridge.target_pack for bridge in self.bridges
             if bridge.source_pack == source_pack}
        ))

    def to_engine(self) -> FederationEngine:
        """Upgrade this legacy federation to a full :class:`FederationEngine`.

        Returns
        -------
        FederationEngine
            An engine backed by the merged catalog and these bridges.
        """
        return FederationEngine(
            catalog=self.merged_catalog(),
            bridges=self.bridges,
        )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "EvidenceKindLabel",
    "FederationCache",
    "FederationCoordinator",
    "FederationDiagnostics",
    "FederationEngine",
    "FederationHistory",
    "FederationPlan",
    "FederationRequest",
    "FederationResult",
    "FederationSerializer",
    "FederationStatus",
    "FederationValidator",
    "EvidenceCombiner",
    "FEDERATION_SPEC_PROVENANCE",
    "PackFederation",
    # Cross-subsystem enrichments
    "descent_federation",
    "solver_federation",
]


# ---------------------------------------------------------------------------
# Cross-subsystem enrichment functions
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.descent import (
        DescentEngine as _DescentEngine,
        DescentStrategy as _DescentStrategy,
    )
except Exception:  # pragma: no cover
    _DescentEngine = None  # type: ignore[assignment,misc]
    _DescentStrategy = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.z3_session import Z3Session as _Z3Session, SolveOutcome as _SolveOutcome
except Exception:  # pragma: no cover
    _Z3Session = None  # type: ignore[assignment,misc]
    _SolveOutcome = None  # type: ignore[assignment,misc]


def descent_federation(
    request: FederationRequest,
    *,
    cover: Any | None = None,
    local_sections: Mapping[str, Any] | None = None,
    strategy: Any | None = None,
) -> dict[str, Any]:
    """Federate across pack boundaries using descent conditions.

    Applies ``jugeo.geometry.descent.DescentEngine`` to verify that
    evidence from multiple packs satisfies the sheaf gluing condition
    before combining.  This ensures kind preservation across the
    federation boundary.

    Parameters
    ----------
    request:
        The federation request describing packs and propositions.
    cover:
        A ``Cover`` over which descent is checked.
    local_sections:
        Mapping from pack/section keys to local section objects.
    strategy:
        A ``DescentStrategy``; defaults to *EAGER*.

    Returns
    -------
    dict[str, Any]
        ``{"status": str, "descent_ok": bool, "obstructions": list,
        "participating_packs": list}``.
    """
    secs = dict(local_sections or {})
    strat = strategy
    if strat is None and _DescentStrategy is not None:
        strat = _DescentStrategy.EAGER

    packs = list(getattr(request, "pack_ids", ()) or ())
    if not packs:
        packs = list(getattr(request, "contributing_packs", ()) or ())

    result: dict[str, Any] = {
        "status": "failed",
        "descent_ok": False,
        "obstructions": [],
        "participating_packs": packs,
    }

    if _DescentEngine is None:
        result["status"] = "unavailable"
        result["obstructions"] = ["jugeo.geometry.descent not available"]
        return result

    if cover is None:
        result["status"] = "no_cover"
        result["obstructions"] = ["No cover provided for descent federation"]
        return result

    try:
        engine = _DescentEngine()
        if hasattr(engine, "attempt_descent"):
            descent_result = engine.attempt_descent(cover, secs)
        elif hasattr(engine, "run"):
            descent_result = engine.run(cover, secs)
        else:
            result["obstructions"] = ["DescentEngine has no run method"]
            return result

        if hasattr(descent_result, "status"):
            sv = descent_result.status
            result["status"] = sv.value if hasattr(sv, "value") else str(sv)
        else:
            result["status"] = "success"

        obs = getattr(descent_result, "obstructions", None)
        result["obstructions"] = list(obs) if obs else []
        result["descent_ok"] = result["status"] in ("success", "SUCCESS")

    except Exception as exc:
        result["status"] = "error"
        result["obstructions"] = [str(exc)]

    return result


def solver_federation(
    request: FederationRequest,
    *,
    formulas: Sequence[str] | None = None,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Use the Z3 solver for cross-pack verification.

    Opens a ``jugeo.solver.z3_session.Z3Session`` and checks each
    formula (one per bridge crossing) for satisfiability.  All formulas
    must be satisfiable for the federation to succeed.

    Parameters
    ----------
    request:
        The federation request.
    formulas:
        SMT-LIB formula strings, one per bridge theorem to verify.
    timeout_ms:
        Solver timeout in milliseconds.

    Returns
    -------
    dict[str, Any]
        ``{"status": str, "all_sat": bool, "results": list[dict],
        "participating_packs": list}``.
    """
    packs = list(getattr(request, "pack_ids", ()) or ())
    if not packs:
        packs = list(getattr(request, "contributing_packs", ()) or ())

    output: dict[str, Any] = {
        "status": "failed",
        "all_sat": False,
        "results": [],
        "participating_packs": packs,
    }

    if _Z3Session is None:
        output["status"] = "unavailable"
        output["results"] = [{"error": "jugeo.solver.z3_session not available"}]
        return output

    fmls = list(formulas or [])
    if not fmls:
        fmls = ["true"]

    all_sat = True
    per_formula: list[dict[str, Any]] = []

    for formula in fmls:
        session = _Z3Session(timeout_ms=timeout_ms)
        try:
            session.assert_formula(formula)
            outcome = session.check_sat()
            outcome_str = outcome.value if hasattr(outcome, "value") else str(outcome)
            sat = outcome_str.upper() == "SAT"
            if not sat:
                all_sat = False
            per_formula.append({
                "formula": formula,
                "outcome": outcome_str,
                "satisfiable": sat,
            })
        except Exception as exc:
            all_sat = False
            per_formula.append({
                "formula": formula,
                "outcome": "error",
                "satisfiable": False,
                "error": str(exc),
            })
        finally:
            if hasattr(session, "close"):
                session.close()

    output["all_sat"] = all_sat
    output["results"] = per_formula
    output["status"] = "success" if all_sat else "partial"

    return output


# copilot: shared-core marker for future LLM orchestration.
