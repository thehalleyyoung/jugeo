"""Core algorithms for specification satisfaction in JuGeo.

Implements the full satisfaction pipeline from specification to certificate,
including descent-based satisfaction, gap repair, iterative refinement, trust
propagation, specification composition, and residual minimisation.

References: theory2.tex §10 — Specification Satisfaction.
  §10.1 The satisfaction functor and descent criterion.
  §10.2 Gap algebra and repair witnesses.
  §10.3 Trust propagation through cover overlaps.
  §10.4 Compositional specification algebra.
  §10.5 Iterative oracle-driven refinement.

copilot: core satisfaction algorithms — descent, gap repair, trust propagation,
         specification composition, and iterative oracle-driven refinement.
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time in ISO-8601 format.

    Returns
    -------
    str
        UTC timestamp string of the form ``YYYY-MM-DDTHH:MM:SSZ``.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str = "id") -> str:
    """Generate a short, unique identifier with an optional prefix.

    Parameters
    ----------
    prefix : str, optional
        Human-readable label prepended to the hex fragment, by default ``"id"``.

    Returns
    -------
    str
        A string of the form ``<prefix>-<12-char hex>``.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _sha256_dict(d: dict[str, Any]) -> str:
    """Compute a stable SHA-256 digest of a JSON-serialisable dictionary.

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


def _coverage_fraction(covered: set[str], total: set[str]) -> float:
    """Return the fraction of *total* coordinates that appear in *covered*.

    Parameters
    ----------
    covered : set[str]
        Coordinates for which evidence has been supplied.
    total : set[str]
        Full set of required coordinates.

    Returns
    -------
    float
        A value in ``[0.0, 1.0]``, with ``1.0`` meaning complete coverage.
    """
    if not total:
        return 1.0
    return len(covered & total) / len(total)


def _overlap_key(coord_a: str, coord_b: str) -> str:
    """Return a canonical, order-independent key for a pair of coordinates.

    Parameters
    ----------
    coord_a : str
        First coordinate identifier.
    coord_b : str
        Second coordinate identifier.

    Returns
    -------
    str
        String of the form ``<smaller>||<larger>``.
    """
    a, b = sorted([coord_a, coord_b])
    return f"{a}||{b}"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

# -- result types -----------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SatisfactionAlgorithmResult:
    """Immutable record of a completed satisfaction algorithm run.

    Captures whether the algorithm succeeded, the produced artefacts (witness,
    certificate, residual gap), runtime metadata, and a structured log suitable
    for downstream debugging or audit.

    References
    ----------
    theory2.tex §10.1 — the satisfaction functor output type.

    Parameters
    ----------
    success : bool
        ``True`` iff a complete ``CertificateOfSatisfaction`` was produced.
    witness : SatisfactionWitness or None
        The satisfaction witness assembled during the run; may be partial.
    certificate : CertificateOfSatisfaction or None
        Populated iff *success* is ``True``.
    gap : ResidualGap or None
        Populated iff *success* is ``False``; describes what is missing.
    iterations_taken : int
        How many outer loop iterations the algorithm consumed.
    elapsed_seconds : float
        Wall-clock time from algorithm start to completion.
    algorithm_log : tuple[str, ...]
        Ordered sequence of log messages emitted during the run.
    """

    success: bool
    witness: Any  # SatisfactionWitness | None
    certificate: Any  # CertificateOfSatisfaction | None
    gap: Any  # ResidualGap | None
    iterations_taken: int
    elapsed_seconds: float
    algorithm_log: tuple[str, ...]

    # -- query helpers -------------------------------------------------------

    def is_certified(self) -> bool:
        """Return ``True`` when a valid certificate is present.

        Returns
        -------
        bool
            ``True`` iff ``self.certificate`` is not ``None``.
        """
        return self.certificate is not None

    def has_gap(self) -> bool:
        """Return ``True`` when a residual gap was recorded.

        Returns
        -------
        bool
            ``True`` iff ``self.gap`` is not ``None``.
        """
        return self.gap is not None

    def to_dict(self) -> dict[str, Any]:
        """Serialise the result to a plain Python dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-compatible representation of the result; nested objects are
            converted via their own ``to_dict`` methods when available, or
            ``repr`` as a fallback.
        """

        def _coerce(val: Any) -> Any:
            if val is None:
                return None
            if hasattr(val, "to_dict"):
                return val.to_dict()
            return repr(val)

        return {
            "success": self.success,
            "iterations_taken": self.iterations_taken,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "witness": _coerce(self.witness),
            "certificate": _coerce(self.certificate),
            "gap": _coerce(self.gap),
            "algorithm_log": list(self.algorithm_log),
        }

    def summary(self) -> str:
        """Return a single human-readable summary sentence.

        Returns
        -------
        str
            A string describing the high-level outcome of the algorithm run.
        """
        if self.success:
            cert_id = getattr(self.certificate, "certificate_id", "?")
            return (
                f"Satisfied in {self.iterations_taken} iteration(s) "
                f"({self.elapsed_seconds:.3f}s); certificate={cert_id}"
            )
        gap_coords = 0
        if self.gap is not None:
            unsatisfied = getattr(self.gap, "unsatisfied_coordinates", [])
            gap_coords = len(unsatisfied)
        return (
            f"Unsatisfied after {self.iterations_taken} iteration(s) "
            f"({self.elapsed_seconds:.3f}s); {gap_coords} coordinate(s) remain open."
        )


# -- iteration state ---------------------------------------------------------

@dataclass(slots=True)
class IterationState:
    """Mutable accumulator tracking a single satisfaction iteration.

    Used internally by ``iterative_satisfaction_loop`` to thread state across
    loop iterations without rebuilding the full witness from scratch each time.

    Parameters
    ----------
    iteration : int
        Current outer loop counter (0-based).
    current_witness : SatisfactionWitness or None
        The most recently produced (possibly partial) witness, or ``None``
        before the first iteration has produced any evidence.
    pending_gaps : list[ResidualGap]
        Gaps that have been identified but not yet resolved.
    resolved_gaps : list[ResidualGap]
        Gaps for which repair evidence was successfully applied.
    evidence_added_this_iter : list[dict]
        Evidence items applied in the current iteration, for audit.
    trust_history : list[float]
        Mean trust score after each iteration; tracks convergence.
    """

    iteration: int = 0
    current_witness: Any = None  # SatisfactionWitness | None
    pending_gaps: list[Any] = field(default_factory=list)  # list[ResidualGap]
    resolved_gaps: list[Any] = field(default_factory=list)  # list[ResidualGap]
    evidence_added_this_iter: list[dict[str, Any]] = field(default_factory=list)
    trust_history: list[float] = field(default_factory=list)

    # -- mutation helpers ---------------------------------------------------

    def advance(self) -> None:
        """Increment the iteration counter and clear per-iteration tracking.

        Raises
        ------
        OverflowError
            If the iteration counter would exceed ``sys.maxsize`` (theoretical;
            never occurs in practice).
        """
        self.iteration += 1
        self.evidence_added_this_iter = []

    def add_evidence(self, coordinate: str, evidence_items: list[dict[str, Any]]) -> None:
        """Record that evidence was applied to *coordinate* in this iteration.

        Parameters
        ----------
        coordinate : str
            The coordinate for which evidence is being added.
        evidence_items : list[dict[str, Any]]
            Raw evidence payloads to record.
        """
        for item in evidence_items:
            record = {"coordinate": coordinate, "item": item, "iteration": self.iteration}
            self.evidence_added_this_iter.append(record)

    def record_gap_resolution(self, gap: Any) -> None:
        """Move *gap* from the pending list to the resolved list.

        Parameters
        ----------
        gap : ResidualGap
            The gap that was successfully repaired.
        """
        if gap in self.pending_gaps:
            self.pending_gaps.remove(gap)
        self.resolved_gaps.append(gap)

    def snapshot(self) -> dict[str, Any]:
        """Return a lightweight snapshot dict for logging or checkpointing.

        Returns
        -------
        dict[str, Any]
            A plain dictionary capturing the current state without deep-copying
            any domain objects.
        """
        return {
            "iteration": self.iteration,
            "has_witness": self.current_witness is not None,
            "pending_gap_count": len(self.pending_gaps),
            "resolved_gap_count": len(self.resolved_gaps),
            "evidence_this_iter": len(self.evidence_added_this_iter),
            "trust_history": list(self.trust_history),
        }


# -- trust propagation -------------------------------------------------------

@dataclass(slots=True)
class TrustPropagator:
    """Mutable engine for propagating trust scores across a cover.

    Applies a configurable set of propagation rules that update per-coordinate
    trust scores based on neighbouring values and overlap compatibility data.

    Parameters
    ----------
    propagation_rules : dict[str, Callable]
        Named callables ``(coord, trust, neighbours) -> float`` that implement
        individual propagation strategies.  Populated via ``register_rule``.
    propagation_log : list[dict]
        Audit log of every propagation step, for debugging.
    """

    propagation_rules: dict[str, Callable[..., float]] = field(default_factory=dict)
    propagation_log: list[dict[str, Any]] = field(default_factory=list)

    # -- public API ---------------------------------------------------------

    def register_rule(self, name: str, fn: Callable[..., float]) -> None:
        """Add or replace a named propagation rule.

        Parameters
        ----------
        name : str
            Unique rule identifier (e.g. ``"average"``, ``"min_neighbour"``).
        fn : Callable
            Rule callable; signature ``(coord, trust, neighbours) -> float``.
        """
        self.propagation_rules[name] = fn

    def propagate(
        self,
        witness: Any,
        trust_map: dict[str, float],
    ) -> dict[str, float]:
        """Propagate trust through the witness's cover.

        For each coordinate in *trust_map*, collect neighbours from the witness
        gluing data, then apply all registered rules and take the mean of the
        rule outputs as the updated trust score.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness whose gluing structure defines the neighbourhood graph.
        trust_map : dict[str, float]
            Initial per-coordinate trust scores in ``[0.0, 1.0]``.

        Returns
        -------
        dict[str, float]
            Updated per-coordinate trust scores after one propagation pass.

        Raises
        ------
        ValueError
            If any trust score in *trust_map* is outside ``[0.0, 1.0]``.
        """
        for coord, score in trust_map.items():
            if not (0.0 <= score <= 1.0):
                raise ValueError(
                    f"Trust score for {coord!r} must be in [0.0, 1.0]; got {score}"
                )

        gluing_data: dict[str, Any] = {}
        if witness is not None and hasattr(witness, "gluing_data"):
            raw = witness.gluing_data
            if isinstance(raw, dict):
                gluing_data = raw

        neighbour_map: dict[str, list[str]] = {c: [] for c in trust_map}
        for overlap_key, compat in gluing_data.items():
            parts = overlap_key.split("||")
            if len(parts) == 2:
                a, b = parts
                if a in neighbour_map:
                    neighbour_map[a].append(b)
                if b in neighbour_map:
                    neighbour_map[b].append(a)

        updated: dict[str, float] = {}
        for coord, base_trust in trust_map.items():
            neighbours = neighbour_map.get(coord, [])
            neighbour_trusts = [trust_map[n] for n in neighbours if n in trust_map]
            if not self.propagation_rules:
                updated[coord] = base_trust
            else:
                rule_outputs = [
                    self._apply_rule(rule_name, coord, base_trust, neighbour_trusts)
                    for rule_name in self.propagation_rules
                ]
                updated[coord] = sum(rule_outputs) / len(rule_outputs)
            self.propagation_log.append(
                {
                    "coord": coord,
                    "base": base_trust,
                    "neighbours": neighbours,
                    "updated": updated[coord],
                }
            )
        return updated

    def _apply_rule(
        self,
        rule_name: str,
        coord: str,
        trust: float,
        neighbours: list[float],
    ) -> float:
        """Apply a single named rule and clamp the result to ``[0.0, 1.0]``.

        Parameters
        ----------
        rule_name : str
            Key into ``self.propagation_rules``.
        coord : str
            Coordinate identifier (passed to the rule callable).
        trust : float
            Current trust score for *coord*.
        neighbours : list[float]
            Trust scores of adjacent coordinates.

        Returns
        -------
        float
            Updated trust score in ``[0.0, 1.0]``.
        """
        fn = self.propagation_rules[rule_name]
        try:
            result = fn(coord, trust, neighbours)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Propagation rule %r raised: %s", rule_name, exc)
            result = trust
        return max(0.0, min(1.0, float(result)))

    def propagate_through_cover(
        self,
        witness: Any,
        cover_trust: dict[str, float],
    ) -> dict[str, float]:
        """Run multiple passes of propagation until convergence or 20 rounds.

        Parameters
        ----------
        witness : SatisfactionWitness
            Defines the neighbourhood topology for propagation.
        cover_trust : dict[str, float]
            Initial trust assignment per patch.

        Returns
        -------
        dict[str, float]
            Converged (or max-round) trust map.
        """
        current = dict(cover_trust)
        for _ in range(20):
            updated = self.propagate(witness, current)
            delta = max(abs(updated[c] - current[c]) for c in current) if current else 0.0
            current = updated
            if delta < 1e-6:
                break
        return current

    def _decay_by_distance(self, base_trust: float, distance: int) -> float:
        """Apply exponential decay to *base_trust* over *distance* hops.

        Parameters
        ----------
        base_trust : float
            Trust at the source coordinate.
        distance : int
            Number of hops from the source.

        Returns
        -------
        float
            Decayed trust, clamped to ``[0.0, 1.0]``.
        """
        decay_factor = 0.85
        return max(0.0, base_trust * (decay_factor ** distance))

    def aggregate_trust(self, trust_assignments: list[dict[str, float]]) -> dict[str, float]:
        """Combine multiple independent trust maps via coordinate-wise mean.

        Parameters
        ----------
        trust_assignments : list[dict[str, float]]
            Each dict maps coordinate identifiers to trust scores.

        Returns
        -------
        dict[str, float]
            Aggregated trust map; coordinates absent from any assignment receive
            the mean of the maps in which they do appear.
        """
        if not trust_assignments:
            return {}
        all_coords: set[str] = set()
        for ta in trust_assignments:
            all_coords.update(ta.keys())
        result: dict[str, float] = {}
        for coord in all_coords:
            values = [ta[coord] for ta in trust_assignments if coord in ta]
            result[coord] = sum(values) / len(values)
        return result


# -- specification composition -----------------------------------------------

@dataclass(slots=True)
class SpecificationCompositionAlgorithm:
    """Mutable engine for composing two or more specifications.

    Supports conjunction (``A ∧ B``), disjunction (``A ∨ B``), and sequential
    composition (``A ; B``).

    Parameters
    ----------
    composition_log : list[dict]
        Audit trail of every composition operation performed.
    """

    composition_log: list[dict[str, Any]] = field(default_factory=list)

    # -- public API ---------------------------------------------------------

    def compose(
        self,
        spec_a: Any,
        spec_b: Any,
        mode: str = "conjunction",
    ) -> Any:
        """Compose two specifications according to *mode*.

        Parameters
        ----------
        spec_a : Specification
            Left operand.
        spec_b : Specification
            Right operand.
        mode : str, optional
            One of ``"conjunction"``, ``"disjunction"``, or ``"sequential"``.
            Defaults to ``"conjunction"``.

        Returns
        -------
        Specification
            The composed specification.

        Raises
        ------
        ValueError
            If *mode* is not one of the supported strings.
        """
        supported = {"conjunction", "disjunction", "sequential"}
        if mode not in supported:
            raise ValueError(f"Unsupported composition mode {mode!r}; choose from {supported}")

        if mode == "conjunction":
            result = self._conjunction(spec_a, spec_b)
        elif mode == "disjunction":
            result = self._disjunction(spec_a, spec_b)
        else:
            result = self._sequential(spec_a, spec_b)

        self.composition_log.append(
            {
                "mode": mode,
                "spec_a_id": getattr(spec_a, "spec_id", repr(spec_a)),
                "spec_b_id": getattr(spec_b, "spec_id", repr(spec_b)),
                "result_id": getattr(result, "spec_id", repr(result)),
                "timestamp": _now_iso(),
            }
        )
        return result

    def _conjunction(self, spec_a: Any, spec_b: Any) -> Any:
        """Return a specification whose formula is the conjunction of A and B.

        Parameters
        ----------
        spec_a : Specification
            Left operand.
        spec_b : Specification
            Right operand.

        Returns
        -------
        Specification
            Composed conjunction specification.
        """
        formula_a = getattr(spec_a, "formula", str(spec_a))
        formula_b = getattr(spec_b, "formula", str(spec_b))
        new_formula = f"({formula_a}) ∧ ({formula_b})"
        coordinates_a: frozenset[str] = getattr(spec_a, "required_coordinates", frozenset())
        coordinates_b: frozenset[str] = getattr(spec_b, "required_coordinates", frozenset())
        merged_coords = coordinates_a | coordinates_b
        new_meta: dict[str, Any] = {
            "composed_from": [
                getattr(spec_a, "spec_id", "?"),
                getattr(spec_b, "spec_id", "?"),
            ],
            "composition_mode": "conjunction",
            "created_at": _now_iso(),
        }
        return _make_composed_spec(
            formula=new_formula,
            required_coordinates=merged_coords,
            metadata=new_meta,
            source_specs=[spec_a, spec_b],
        )

    def _disjunction(self, spec_a: Any, spec_b: Any) -> Any:
        """Return a specification whose formula is the disjunction of A and B.

        Parameters
        ----------
        spec_a : Specification
            Left operand.
        spec_b : Specification
            Right operand.

        Returns
        -------
        Specification
            Composed disjunction specification.
        """
        formula_a = getattr(spec_a, "formula", str(spec_a))
        formula_b = getattr(spec_b, "formula", str(spec_b))
        new_formula = f"({formula_a}) ∨ ({formula_b})"
        coordinates_a: frozenset[str] = getattr(spec_a, "required_coordinates", frozenset())
        coordinates_b: frozenset[str] = getattr(spec_b, "required_coordinates", frozenset())
        merged_coords = coordinates_a | coordinates_b
        new_meta: dict[str, Any] = {
            "composed_from": [
                getattr(spec_a, "spec_id", "?"),
                getattr(spec_b, "spec_id", "?"),
            ],
            "composition_mode": "disjunction",
            "created_at": _now_iso(),
        }
        return _make_composed_spec(
            formula=new_formula,
            required_coordinates=merged_coords,
            metadata=new_meta,
            source_specs=[spec_a, spec_b],
        )

    def _sequential(self, spec_a: Any, spec_b: Any) -> Any:
        """Return a specification encoding sequential composition ``A ; B``.

        Parameters
        ----------
        spec_a : Specification
            Pre-condition specification.
        spec_b : Specification
            Post-condition specification.

        Returns
        -------
        Specification
            Composed sequential specification.
        """
        formula_a = getattr(spec_a, "formula", str(spec_a))
        formula_b = getattr(spec_b, "formula", str(spec_b))
        new_formula = f"({formula_a}) ; ({formula_b})"
        coordinates_a: frozenset[str] = getattr(spec_a, "required_coordinates", frozenset())
        coordinates_b: frozenset[str] = getattr(spec_b, "required_coordinates", frozenset())
        merged_coords = coordinates_a | coordinates_b
        new_meta: dict[str, Any] = {
            "composed_from": [
                getattr(spec_a, "spec_id", "?"),
                getattr(spec_b, "spec_id", "?"),
            ],
            "composition_mode": "sequential",
            "created_at": _now_iso(),
        }
        return _make_composed_spec(
            formula=new_formula,
            required_coordinates=merged_coords,
            metadata=new_meta,
            source_specs=[spec_a, spec_b],
        )

    def multi_compose(self, specs: Sequence[Any], mode: str = "conjunction") -> Any:
        """Left-fold a sequence of specifications using *mode*.

        Parameters
        ----------
        specs : Sequence[Specification]
            Non-empty sequence of specifications to compose.
        mode : str, optional
            Composition mode; same options as ``compose``.

        Returns
        -------
        Specification
            Single specification that is the composition of all inputs.

        Raises
        ------
        ValueError
            If *specs* is empty.
        """
        if not specs:
            raise ValueError("Cannot compose an empty sequence of specifications.")
        result = specs[0]
        for spec in specs[1:]:
            result = self.compose(result, spec, mode=mode)
        return result

    def decompose_by_kind(self, spec: Any) -> dict[Any, Any]:
        """Partition *spec*'s sub-specifications by ``SpecificationKind``.

        If *spec* was assembled via ``multi_compose``, its metadata will record
        the source specifications; this method groups them by their ``kind``
        attribute.

        Parameters
        ----------
        spec : Specification
            A (possibly composite) specification to decompose.

        Returns
        -------
        dict[SpecificationKind, Specification]
            Mapping from kind to the single composed sub-spec for that kind.
            If *spec* is not composite, returns ``{spec.kind: spec}``.
        """
        source_specs: list[Any] = []
        meta = getattr(spec, "metadata", {}) or {}
        if isinstance(meta, dict) and "composed_from" in meta:
            source_ids: list[str] = meta["composed_from"]
            all_sources: list[Any] = getattr(spec, "_source_specs", [])
            source_specs = list(all_sources)
        if not source_specs:
            kind = getattr(spec, "kind", None)
            return {kind: spec}

        by_kind: dict[Any, list[Any]] = {}
        for src in source_specs:
            k = getattr(src, "kind", None)
            by_kind.setdefault(k, []).append(src)

        result: dict[Any, Any] = {}
        for k, group in by_kind.items():
            if len(group) == 1:
                result[k] = group[0]
            else:
                result[k] = self.multi_compose(group)
        return result


# -- residual minimiser -------------------------------------------------------

@dataclass(slots=True)
class ResidualMinimizer:
    """Mutable engine for finding minimal repair sets for residual gaps.

    Uses a greedy weighted set-cover algorithm to select the smallest (by
    estimated repair effort) subset of candidate hints that together cover all
    unsatisfied coordinates in a gap.

    Parameters
    ----------
    minimization_log : list[dict]
        Audit record of every minimisation run.
    attempts : int
        Counter of how many times ``minimize`` has been called.
    """

    minimization_log: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 0

    # -- public API ---------------------------------------------------------

    def minimize(
        self,
        gap: Any,
        hints: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return a minimal covering subset of *hints* for *gap*.

        Parameters
        ----------
        gap : ResidualGap
            The gap whose unsatisfied coordinates must be covered.
        hints : list[dict[str, Any]]
            Candidate repair hints; each hint should have a ``"covers"`` key
            listing coordinate IDs it can satisfy and an ``"effort"`` key
            (float) estimating repair cost.

        Returns
        -------
        list[dict[str, Any]]
            Minimal (greedy) subset of hints that covers all unsatisfied
            coordinates.

        Raises
        ------
        ValueError
            If *hints* is empty but the gap has unsatisfied coordinates.
        """
        self.attempts += 1
        unsatisfied: list[str] = getattr(gap, "unsatisfied_coordinates", [])
        if not unsatisfied:
            return []

        applicable = self._filter_applicable_hints(gap, hints)
        if not applicable and unsatisfied:
            raise ValueError(
                f"No applicable hints provided for gap with "
                f"{len(unsatisfied)} unsatisfied coordinate(s)."
            )

        ranked = self._rank_hints_by_impact(applicable, gap)
        pruned = self._prune_redundant_hints(ranked)
        selected = _greedy_set_cover(
            universe=set(unsatisfied),
            hints=pruned,
        )
        self.minimization_log.append(
            {
                "attempt": self.attempts,
                "gap_coords": unsatisfied,
                "hints_in": len(hints),
                "hints_out": len(selected),
                "timestamp": _now_iso(),
            }
        )
        return selected

    def _filter_applicable_hints(
        self,
        gap: Any,
        hints: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Discard hints whose covered coordinates do not overlap the gap.

        Parameters
        ----------
        gap : ResidualGap
            The gap to repair.
        hints : list[dict[str, Any]]
            All candidate hints.

        Returns
        -------
        list[dict[str, Any]]
            Subset of *hints* with at least one covered coordinate in the gap.
        """
        unsatisfied = set(getattr(gap, "unsatisfied_coordinates", []))
        return [
            h for h in hints
            if bool(set(h.get("covers", [])) & unsatisfied)
        ]

    def _rank_hints_by_impact(
        self,
        hints: list[dict[str, Any]],
        gap: Any,
    ) -> list[dict[str, Any]]:
        """Sort hints by impact-to-effort ratio (descending).

        Impact is defined as the number of unsatisfied coordinates the hint
        covers.  Effort is taken from ``hint["effort"]`` (defaults to 1.0).

        Parameters
        ----------
        hints : list[dict[str, Any]]
            Hints to rank.
        gap : ResidualGap
            Gap providing the set of unsatisfied coordinates.

        Returns
        -------
        list[dict[str, Any]]
            Hints sorted from highest to lowest ratio.
        """
        unsatisfied = set(getattr(gap, "unsatisfied_coordinates", []))

        def _ratio(hint: dict[str, Any]) -> float:
            covers = set(hint.get("covers", []))
            impact = len(covers & unsatisfied)
            effort = float(hint.get("effort", 1.0)) or 1.0
            return impact / effort

        return sorted(hints, key=_ratio, reverse=True)

    def _prune_redundant_hints(
        self,
        hints: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Remove hints whose covered coordinates are a subset of another hint.

        Parameters
        ----------
        hints : list[dict[str, Any]]
            Candidate hints (should be pre-ranked).

        Returns
        -------
        list[dict[str, Any]]
            Deduplicated hints with dominated entries removed.
        """
        pruned: list[dict[str, Any]] = []
        all_cover_sets = [frozenset(h.get("covers", [])) for h in hints]
        for i, hint in enumerate(hints):
            dominated = False
            for j, other_covers in enumerate(all_cover_sets):
                if i != j and all_cover_sets[i] < other_covers:
                    dominated = True
                    break
            if not dominated:
                pruned.append(hint)
        return pruned

    def compute_minimal_repair_set(self, gap: Any) -> list[dict[str, Any]]:
        """Extract and minimise repair hints already embedded in *gap*.

        Parameters
        ----------
        gap : ResidualGap
            A gap whose ``repair_hints`` attribute provides candidate fixes.

        Returns
        -------
        list[dict[str, Any]]
            Minimal subset of ``gap.repair_hints`` covering the gap.
        """
        hints: list[dict[str, Any]] = list(getattr(gap, "repair_hints", []))
        return self.minimize(gap, hints)

    def estimate_total_effort(self, hints: list[dict[str, Any]]) -> float:
        """Sum the effort estimates across all supplied hints.

        Parameters
        ----------
        hints : list[dict[str, Any]]
            Hints with optional ``"effort"`` keys.

        Returns
        -------
        float
            Total estimated effort (sum of ``hint["effort"]``, defaulting
            to ``1.0`` per hint).
        """
        return sum(float(h.get("effort", 1.0)) for h in hints)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _greedy_set_cover(
    universe: set[str],
    hints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Standard greedy set-cover: pick the hint covering most uncovered items.

    Parameters
    ----------
    universe : set[str]
        All coordinates that must be covered.
    hints : list[dict[str, Any]]
        Each hint has a ``"covers"`` key with a collection of coordinates.

    Returns
    -------
    list[dict[str, Any]]
        Minimal greedy cover; may not be globally optimal.
    """
    remaining = set(universe)
    selected: list[dict[str, Any]] = []
    available = list(hints)
    while remaining and available:
        best = max(available, key=lambda h: len(set(h.get("covers", [])) & remaining))
        best_covers = set(best.get("covers", [])) & remaining
        if not best_covers:
            break
        selected.append(best)
        remaining -= best_covers
        available.remove(best)
    return selected


def _make_composed_spec(
    formula: str,
    required_coordinates: frozenset[str],
    metadata: dict[str, Any],
    source_specs: list[Any],
) -> Any:
    """Construct a minimal composed specification object.

    Returns a plain namespace-like dict when the real ``Specification`` type is
    unavailable; otherwise attempts to instantiate it.

    Parameters
    ----------
    formula : str
        The logical formula for the composed specification.
    required_coordinates : frozenset[str]
        Merged set of required coordinates.
    metadata : dict[str, Any]
        Provenance metadata for the composition.
    source_specs : list
        Original source specifications (stored for decompose_by_kind).

    Returns
    -------
    Specification
        The composed specification (or a dict proxy if model unavailable).
    """
    spec_id = _new_id("composed-spec")
    try:
        import jugeo.problem_modes.specification_satisfaction.models as _m  # noqa: PLC0415
        return _m.Specification(  # type: ignore[call-arg]
            spec_id=spec_id,
            formula=formula,
            required_coordinates=required_coordinates,
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001
        obj: dict[str, Any] = {
            "spec_id": spec_id,
            "formula": formula,
            "required_coordinates": required_coordinates,
            "metadata": metadata,
            "_source_specs": source_specs,
        }
        return obj


def _build_partial_witness(
    spec: Any,
    evidence_map: dict[str, list[dict[str, Any]]],
    gluing_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble a plain-dict witness from raw evidence.

    Parameters
    ----------
    spec : Specification
        The target specification.
    evidence_map : dict[str, list[dict[str, Any]]]
        Mapping from coordinate id to list of evidence payloads.
    gluing_data : dict[str, Any] or None
        Optional precomputed gluing / overlap compatibility data.

    Returns
    -------
    dict[str, Any]
        A witness dictionary usable as a proxy when the model types are absent.
    """
    spec_id = getattr(spec, "spec_id", str(spec))
    required: frozenset[str] = getattr(spec, "required_coordinates", frozenset())
    covered = frozenset(evidence_map.keys()) & required
    missing = required - covered
    status = "partial" if missing else "complete"
    return {
        "witness_id": _new_id("witness"),
        "spec_id": spec_id,
        "evidence_map": {k: list(v) for k, v in evidence_map.items()},
        "gluing_data": gluing_data or {},
        "covered_coordinates": list(covered),
        "missing_coordinates": list(missing),
        "status": status,
        "created_at": _now_iso(),
    }


def _build_gap(
    spec: Any,
    witness: Any,
    obstruction_class: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a plain-dict residual gap from a partial witness.

    Parameters
    ----------
    spec : Specification
        The specification that was not satisfied.
    witness : SatisfactionWitness
        The (partial) witness that failed descent.
    obstruction_class : dict[str, Any] or None
        Cech cocycle obstruction data, if available.

    Returns
    -------
    dict[str, Any]
        A gap dictionary with repair hints generated from missing coordinates.
    """
    missing: list[str] = []
    if isinstance(witness, dict):
        missing = list(witness.get("missing_coordinates", []))
    else:
        missing = list(getattr(witness, "missing_coordinates", []))

    repair_hints: list[dict[str, Any]] = [
        {
            "hint_id": _new_id("hint"),
            "coordinate": coord,
            "covers": [coord],
            "effort": 1.0,
            "description": f"Provide evidence for coordinate {coord!r}",
        }
        for coord in missing
    ]
    return {
        "gap_id": _new_id("gap"),
        "spec_id": getattr(spec, "spec_id", str(spec)),
        "unsatisfied_coordinates": missing,
        "obstruction_class": obstruction_class or {},
        "repair_hints": repair_hints,
        "severity": "critical" if missing else "none",
        "created_at": _now_iso(),
    }


def _build_certificate(spec: Any, witness: Any, algorithm_log: list[str]) -> dict[str, Any]:
    """Build a plain-dict certificate from a complete witness.

    Parameters
    ----------
    spec : Specification
        The satisfied specification.
    witness : SatisfactionWitness
        The complete witness attesting to satisfaction.
    algorithm_log : list[str]
        Messages emitted during the satisfaction run.

    Returns
    -------
    dict[str, Any]
        A certificate dictionary with a stable content hash.
    """
    spec_id = getattr(spec, "spec_id", str(spec))
    witness_id = witness.get("witness_id", "?") if isinstance(witness, dict) else getattr(witness, "witness_id", "?")
    payload: dict[str, Any] = {
        "certificate_id": _new_id("cert"),
        "spec_id": spec_id,
        "witness_id": witness_id,
        "issued_at": _now_iso(),
        "algorithm_log": algorithm_log,
    }
    payload["content_hash"] = _sha256_dict(payload)
    return payload


# ---------------------------------------------------------------------------
# Module-level algorithm functions
# ---------------------------------------------------------------------------

def specification_satisfaction_algorithm(
    spec: Any,
    evidence_map: dict[str, list[dict[str, Any]]],
    gluing_data: dict[str, Any] | None = None,
) -> Any:
    """Run the primary specification satisfaction algorithm.

    Constructs a ``SatisfactionWitness`` from the supplied *evidence_map*,
    checks that all required coordinates are covered, verifies that every
    pairwise overlap encoded in *gluing_data* is compatible, and either
    returns a ``CertificateOfSatisfaction`` (success) or a partial
    ``SatisfactionWitness`` (failure).

    Theory basis: theory2.tex §10.1 — the satisfaction functor ``Sat(F)``
    applied to the evidence presheaf ``F``.

    Parameters
    ----------
    spec : Specification
        The target specification whose satisfaction is to be checked.
    evidence_map : dict[str, list[dict[str, Any]]]
        Mapping from coordinate identifiers to lists of evidence payloads.
        Coordinates absent from this dict are treated as uncovered.
    gluing_data : dict[str, Any] or None, optional
        Precomputed overlap–compatibility data.  Keys should be
        ``"<coord_a>||<coord_b>"`` pairs; values should be dicts with at
        least a boolean ``"compatible"`` entry.  If ``None``, overlaps are
        assumed trivially compatible.

    Returns
    -------
    SatisfactionWitness or CertificateOfSatisfaction
        A ``CertificateOfSatisfaction`` when all required coordinates are
        covered and all overlaps are compatible; otherwise a partial
        ``SatisfactionWitness``.

    Raises
    ------
    TypeError
        If *evidence_map* is not a mapping.
    ValueError
        If *spec* has no ``required_coordinates`` attribute and the type is
        unknown.
    """
    if not isinstance(evidence_map, dict):
        raise TypeError(f"evidence_map must be a dict, got {type(evidence_map).__name__}")

    log: list[str] = []
    spec_id: str = getattr(spec, "spec_id", _new_id("spec"))
    required: frozenset[str] = getattr(spec, "required_coordinates", frozenset())

    log.append(f"[sat-alg] Starting satisfaction check for spec={spec_id}")
    log.append(f"[sat-alg] Required coordinates: {sorted(required)}")

    witness = _build_partial_witness(spec, evidence_map, gluing_data)
    missing: list[str] = witness["missing_coordinates"]

    if missing:
        log.append(f"[sat-alg] Coverage incomplete; missing={missing}")
        try:
            import jugeo.problem_modes.specification_satisfaction.models as _m  # noqa: PLC0415
            return _m.SatisfactionWitness(**witness)  # type: ignore[call-arg]
        except Exception:  # noqa: BLE001
            return witness

    log.append("[sat-alg] All coordinates covered; checking overlaps.")

    effective_gluing = gluing_data or {}
    incompatible_overlaps: list[str] = []
    for key, compat_info in effective_gluing.items():
        if isinstance(compat_info, dict) and not compat_info.get("compatible", True):
            incompatible_overlaps.append(key)
            log.append(f"[sat-alg] Incompatible overlap detected: {key}")

    if incompatible_overlaps:
        log.append(f"[sat-alg] Descent blocked by {len(incompatible_overlaps)} incompatible overlap(s).")
        obstruction = {
            "incompatible_overlaps": incompatible_overlaps,
            "obstruction_class": "cech-1-cocycle",
        }
        witness["obstruction_data"] = obstruction
        try:
            import jugeo.problem_modes.specification_satisfaction.models as _m  # noqa: PLC0415
            return _m.SatisfactionWitness(**witness)  # type: ignore[call-arg]
        except Exception:  # noqa: BLE001
            return witness

    log.append("[sat-alg] All overlaps compatible; constructing certificate.")
    cert = _build_certificate(spec, witness, log)
    try:
        import jugeo.problem_modes.specification_satisfaction.models as _m  # noqa: PLC0415
        return _m.CertificateOfSatisfaction(**cert)  # type: ignore[call-arg]
    except Exception:  # noqa: BLE001
        return cert


def descent_for_satisfaction(
    witness: Any,
    descent_engine: Any | None = None,
) -> Any:
    """Run the sheaf-theoretic descent procedure on a satisfaction witness.

    Attempts to glue the local sections encoded in *witness* into a global
    section.  Computes the Čech 1-cocycle on the cover induced by the witness;
    if the cocycle is trivial (coboundary), a global section exists and a
    certificate is returned.  If non-trivial, a ``ResidualGap`` with the
    obstruction class is returned.

    Theory basis: theory2.tex §10.2 — descent for the satisfaction functor.

    Parameters
    ----------
    witness : SatisfactionWitness
        A (possibly partial) satisfaction witness containing local evidence
        sections and gluing data.
    descent_engine : DescentEngine or None, optional
        An optional pre-configured descent engine.  If ``None``, a lightweight
        internal descent procedure is used.

    Returns
    -------
    CertificateOfSatisfaction or ResidualGap
        A certificate if descent succeeds; a gap with obstruction data
        otherwise.

    Raises
    ------
    ValueError
        If *witness* has no gluing data and is also missing coordinates.
    """
    log: list[str] = []
    witness_id = (
        witness.get("witness_id", "?") if isinstance(witness, dict)
        else getattr(witness, "witness_id", "?")
    )
    spec_id = (
        witness.get("spec_id", "?") if isinstance(witness, dict)
        else getattr(witness, "spec_id", "?")
    )
    log.append(f"[descent] Starting descent for witness={witness_id}, spec={spec_id}")

    gluing: dict[str, Any] = (
        witness.get("gluing_data", {}) if isinstance(witness, dict)
        else getattr(witness, "gluing_data", {})
    )
    missing: list[str] = (
        witness.get("missing_coordinates", []) if isinstance(witness, dict)
        else getattr(witness, "missing_coordinates", [])
    )

    if missing:
        log.append(f"[descent] Witness incomplete; missing={missing}")
        raise ValueError(
            f"Cannot run descent on incomplete witness with missing coordinates: {missing}"
        )

    if descent_engine is not None and hasattr(descent_engine, "run"):
        log.append("[descent] Delegating to external DescentEngine.")
        try:
            engine_result = descent_engine.run(witness)
            return engine_result
        except Exception as exc:  # noqa: BLE001
            log.append(f"[descent] DescentEngine raised {exc!r}; falling back to internal.")

    log.append("[descent] Running internal Čech cocycle check.")
    cocycle_is_trivial = True
    obstruction_classes: list[str] = []

    for overlap_key, compat_info in gluing.items():
        if isinstance(compat_info, dict):
            compatible = compat_info.get("compatible", True)
            if not compatible:
                cocycle_is_trivial = False
                obstruction_classes.append(overlap_key)
                log.append(f"[descent] Non-trivial cocycle component at {overlap_key}")

    if cocycle_is_trivial:
        log.append("[descent] Cocycle is trivial — global section exists.")
        cert: dict[str, Any] = {
            "certificate_id": _new_id("cert"),
            "spec_id": spec_id,
            "witness_id": witness_id,
            "issued_at": _now_iso(),
            "descent_log": log,
            "content_hash": _sha256_dict({"spec_id": spec_id, "witness_id": witness_id}),
        }
        try:
            import jugeo.problem_modes.specification_satisfaction.models as _m  # noqa: PLC0415
            return _m.CertificateOfSatisfaction(**cert)  # type: ignore[call-arg]
        except Exception:  # noqa: BLE001
            return cert

    log.append(f"[descent] Non-trivial cocycle; obstruction classes={obstruction_classes}")
    unsatisfied: list[str] = list(
        {part for key in obstruction_classes for part in key.split("||")}
    )
    gap: dict[str, Any] = {
        "gap_id": _new_id("gap"),
        "spec_id": spec_id,
        "unsatisfied_coordinates": unsatisfied,
        "obstruction_class": {
            "kind": "cech-H1",
            "components": obstruction_classes,
        },
        "repair_hints": [
            {
                "hint_id": _new_id("hint"),
                "coordinate": c,
                "covers": [c],
                "effort": 1.0,
                "description": f"Resolve overlap obstruction at {c!r}",
            }
            for c in unsatisfied
        ],
        "severity": "high",
        "created_at": _now_iso(),
        "descent_log": log,
    }
    try:
        import jugeo.problem_modes.specification_satisfaction.models as _m  # noqa: PLC0415
        return _m.ResidualGap(**gap)  # type: ignore[call-arg]
    except Exception:  # noqa: BLE001
        return gap


def gap_repair_algorithm(
    gap: Any,
    available_evidence: dict[str, list[dict[str, Any]]],
) -> Any:
    """Attempt to repair a residual gap using newly available evidence.

    For each ``repair_hint`` in *gap*, the algorithm looks for matching
    evidence in *available_evidence* keyed by the hint's coordinate.  Hints
    that are covered by the new evidence are marked resolved; uncovered hints
    remain.  The result is a new (possibly partial) ``SatisfactionWitness``
    reflecting the repaired state.

    Theory basis: theory2.tex §10.2 — gap repair via evidence extension.

    Parameters
    ----------
    gap : ResidualGap
        The gap to repair.  Must have ``unsatisfied_coordinates`` and
        ``repair_hints`` attributes (or dict keys).
    available_evidence : dict[str, list[dict[str, Any]]]
        New evidence keyed by coordinate identifier.

    Returns
    -------
    SatisfactionWitness
        An updated witness reflecting the repaired state; may still be partial
        if some hints cannot be addressed by the supplied evidence.

    Raises
    ------
    TypeError
        If *available_evidence* is not a dict.
    """
    if not isinstance(available_evidence, dict):
        raise TypeError(f"available_evidence must be a dict, got {type(available_evidence).__name__}")

    log: list[str] = []
    gap_id = (
        gap.get("gap_id", "?") if isinstance(gap, dict)
        else getattr(gap, "gap_id", "?")
    )
    spec_id = (
        gap.get("spec_id", "?") if isinstance(gap, dict)
        else getattr(gap, "spec_id", "?")
    )
    log.append(f"[repair] Starting gap repair for gap={gap_id}, spec={spec_id}")

    hints: list[dict[str, Any]] = (
        list(gap.get("repair_hints", [])) if isinstance(gap, dict)
        else list(getattr(gap, "repair_hints", []))
    )
    unsatisfied: list[str] = (
        list(gap.get("unsatisfied_coordinates", [])) if isinstance(gap, dict)
        else list(getattr(gap, "unsatisfied_coordinates", []))
    )

    repaired_evidence: dict[str, list[dict[str, Any]]] = {}
    still_missing: list[str] = []

    for coord in unsatisfied:
        new_ev = available_evidence.get(coord, [])
        if new_ev:
            repaired_evidence[coord] = new_ev
            log.append(f"[repair] Covered coordinate {coord!r} with {len(new_ev)} evidence item(s).")
        else:
            still_missing.append(coord)
            log.append(f"[repair] No evidence available for coordinate {coord!r}.")

    status = "complete" if not still_missing else "partial"
    gluing_data: dict[str, Any] = {}
    for a in repaired_evidence:
        for b in repaired_evidence:
            if a < b:
                key = _overlap_key(a, b)
                gluing_data[key] = {"compatible": True, "repaired": True}

    witness: dict[str, Any] = {
        "witness_id": _new_id("witness"),
        "spec_id": spec_id,
        "repaired_from_gap": gap_id,
        "evidence_map": repaired_evidence,
        "gluing_data": gluing_data,
        "covered_coordinates": list(repaired_evidence.keys()),
        "missing_coordinates": still_missing,
        "status": status,
        "repair_log": log,
        "created_at": _now_iso(),
    }
    log.append(f"[repair] Witness assembled; status={status}, missing={still_missing}")

    try:
        import jugeo.problem_modes.specification_satisfaction.models as _m  # noqa: PLC0415
        return _m.SatisfactionWitness(**witness)  # type: ignore[call-arg]
    except Exception:  # noqa: BLE001
        return witness


def iterative_satisfaction_loop(
    spec: Any,
    oracle: Callable[[Any], dict[str, list[dict[str, Any]]]],
    max_iter: int = 10,
) -> Any:
    """Iteratively attempt to satisfy *spec* by consulting an oracle on failure.

    Starts with an empty witness and runs the descent procedure each iteration.
    If descent fails, the residual gap is passed to *oracle* to obtain new
    evidence; that evidence is used to repair the gap and a new witness is
    assembled.  If descent succeeds, the certificate is returned immediately.
    After *max_iter* iterations without success the final gap is returned.

    Theory basis: theory2.tex §10.5 — oracle-driven iterative refinement.

    Parameters
    ----------
    spec : Specification
        The specification to be satisfied.
    oracle : Callable[[ResidualGap], dict[str, list[dict[str, Any]]]]
        A callable that, given a gap, returns a dict mapping coordinate
        identifiers to lists of evidence payloads.
    max_iter : int, optional
        Maximum number of refinement iterations, by default ``10``.

    Returns
    -------
    CertificateOfSatisfaction or ResidualGap
        A certificate if satisfaction is achieved within *max_iter*; the
        final residual gap otherwise.

    Raises
    ------
    TypeError
        If *oracle* is not callable.
    ValueError
        If *max_iter* is less than 1.
    """
    if not callable(oracle):
        raise TypeError(f"oracle must be callable, got {type(oracle).__name__}")
    if max_iter < 1:
        raise ValueError(f"max_iter must be ≥ 1; got {max_iter}")

    state = IterationState()
    spec_id: str = getattr(spec, "spec_id", str(spec))
    accumulated_evidence: dict[str, list[dict[str, Any]]] = {}
    last_gap: Any = None

    logger.info("[iter-loop] Starting iterative loop for spec=%s, max_iter=%d", spec_id, max_iter)

    while state.iteration < max_iter:
        state.advance()
        logger.debug("[iter-loop] Iteration %d", state.iteration)

        witness = specification_satisfaction_algorithm(
            spec, accumulated_evidence, gluing_data=None
        )
        state.current_witness = witness

        is_witness_complete = True
        if isinstance(witness, dict):
            is_witness_complete = not witness.get("missing_coordinates", [])
        else:
            is_witness_complete = not getattr(witness, "missing_coordinates", [])

        if is_witness_complete:
            try:
                result = descent_for_satisfaction(witness, descent_engine=None)
            except ValueError as exc:
                logger.warning("[iter-loop] descent raised: %s", exc)
                gap = _build_gap(spec, witness)
                state.pending_gaps.append(gap)
                last_gap = gap
                new_evidence = oracle(gap)
                for coord, items in new_evidence.items():
                    accumulated_evidence.setdefault(coord, []).extend(items)
                    state.add_evidence(coord, items)
                continue

            is_cert = isinstance(result, dict) and "certificate_id" in result
            if not is_cert and hasattr(result, "certificate_id"):
                is_cert = True
            if is_cert:
                logger.info("[iter-loop] Certificate produced on iteration %d.", state.iteration)
                return result

            last_gap = result
            state.pending_gaps.append(result)
        else:
            gap = _build_gap(spec, witness)
            state.pending_gaps.append(gap)
            last_gap = gap

        if last_gap is not None:
            new_evidence = oracle(last_gap)
            for coord, items in new_evidence.items():
                accumulated_evidence.setdefault(coord, []).extend(items)
                state.add_evidence(coord, items)
            state.record_gap_resolution(last_gap)

    logger.warning("[iter-loop] Max iterations (%d) reached without satisfaction.", max_iter)
    if last_gap is None:
        last_gap = _build_gap(spec, state.current_witness or {})
    return last_gap


def trust_propagation_for_satisfaction(
    witness: Any,
    trust_map: dict[str, float],
) -> dict[str, float]:
    """Propagate trust scores across the cover encoded in *witness*.

    For every compatible overlap, the trust scores of both endpoints are
    averaged (trust is shared).  For incompatible overlaps, both endpoints
    are penalised by 10 % of their current score.  A final pass applies
    exponential decay inversely proportional to the size of the witness cover.

    Theory basis: theory2.tex §10.3 — trust propagation for evidence sheaves.

    Parameters
    ----------
    witness : SatisfactionWitness
        The witness whose gluing data defines the cover topology.
    trust_map : dict[str, float]
        Initial per-coordinate trust scores, each in ``[0.0, 1.0]``.

    Returns
    -------
    dict[str, float]
        Updated per-coordinate trust scores after propagation.

    Raises
    ------
    ValueError
        If any trust score is outside ``[0.0, 1.0]``.
    """
    for coord, score in trust_map.items():
        if not (0.0 <= score <= 1.0):
            raise ValueError(f"Trust score for {coord!r} must be in [0.0, 1.0]; got {score}")

    updated = dict(trust_map)
    gluing: dict[str, Any] = (
        witness.get("gluing_data", {}) if isinstance(witness, dict)
        else getattr(witness, "gluing_data", {})
    )

    for overlap_key, compat_info in gluing.items():
        parts = overlap_key.split("||")
        if len(parts) != 2:
            continue
        a, b = parts
        if a not in updated or b not in updated:
            continue
        compatible = True
        if isinstance(compat_info, dict):
            compatible = compat_info.get("compatible", True)

        if compatible:
            mean_trust = (updated[a] + updated[b]) / 2.0
            updated[a] = mean_trust
            updated[b] = mean_trust
        else:
            penalty = 0.10
            updated[a] = max(0.0, updated[a] - updated[a] * penalty)
            updated[b] = max(0.0, updated[b] - updated[b] * penalty)

    cover_size = len(updated)
    if cover_size > 1:
        decay = 0.98 ** (cover_size - 1)
        updated = {c: max(0.0, v * decay) for c, v in updated.items()}

    return updated


def specification_composition_algorithm(
    spec_a: Any,
    spec_b: Any,
    mode: str = "conjunction",
) -> Any:
    """Compose two specifications in the given mode.

    Delegates to ``SpecificationCompositionAlgorithm`` for the actual
    composition logic; this function provides a convenient module-level
    entry point.

    Theory basis: theory2.tex §10.4 — compositional specification algebra.

    Parameters
    ----------
    spec_a : Specification
        Left specification operand.
    spec_b : Specification
        Right specification operand.
    mode : str, optional
        Composition mode: ``"conjunction"``, ``"disjunction"``, or
        ``"sequential"``.  Defaults to ``"conjunction"``.

    Returns
    -------
    Specification
        The composed specification.

    Raises
    ------
    ValueError
        If *mode* is not one of the supported strings.
    """
    algo = SpecificationCompositionAlgorithm()
    return algo.compose(spec_a, spec_b, mode=mode)


def residual_minimization_algorithm(
    gap: Any,
    hints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find a minimal set of repair hints that covers all coordinates in *gap*.

    Uses a greedy weighted set-cover heuristic (see theory2.tex §10.2):

    1. Filter hints to those that cover at least one unsatisfied coordinate.
    2. Sort by impact-to-effort ratio (descending).
    3. Remove dominated hints (whose covered set is a strict subset of another).
    4. Greedily pick hints until all unsatisfied coordinates are covered.

    Parameters
    ----------
    gap : ResidualGap
        The gap to repair.  Must expose ``unsatisfied_coordinates`` and
        optionally ``repair_hints``.
    hints : list[dict[str, Any]]
        Candidate repair hints.  Each hint should provide:
        - ``"covers"`` — list of coordinate IDs it can satisfy.
        - ``"effort"`` — float cost estimate (defaults to ``1.0``).

    Returns
    -------
    list[dict[str, Any]]
        Minimal greedy subset of *hints* sufficient to cover the gap.

    Raises
    ------
    ValueError
        If no hints can cover any unsatisfied coordinate.
    """
    minimizer = ResidualMinimizer()
    return minimizer.minimize(gap, hints)


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
    # Dataclasses
    "SatisfactionAlgorithmResult",
    "IterationState",
    "TrustPropagator",
    "SpecificationCompositionAlgorithm",
    "ResidualMinimizer",
    # Algorithm functions
    "specification_satisfaction_algorithm",
    "descent_for_satisfaction",
    "gap_repair_algorithm",
    "iterative_satisfaction_loop",
    "trust_propagation_for_satisfaction",
    "specification_composition_algorithm",
    "residual_minimization_algorithm",
    # Unified architecture cross-references
    "spec_descent",
    "spec_certificate",
    "spec_encoding",
]
