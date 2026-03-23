"""Countermodel extraction, normalization, and obstruction conversion for JuGeo.

When Z3 returns SAT on the negation of a claim the model constitutes a
counterexample — a concrete witness that the claim does not hold at the
given coordinate.  This module (theory2.tex ch11 — Debugging,
counterexamples, repair) extracts those models, normalizes them for
comparison across solver runs, converts them into first-class
:class:`~jugeo.errors.ObstructionRecord` values with actionable repair
hints, and produces regression test cases so that future verification
cycles catch regressions early.

The ``copilot`` integration surfaces appear in
:meth:`CountermodelExplainer.copilot_explanation` and
:meth:`RepairHintGenerator.copilot_suggest_fix`, where the LLM
orchestration layer can request human-readable narratives or structured
fix suggestions for countermodels it discovers during proposal sweeps.

Backward compatibility
~~~~~~~~~~~~~~~~~~~~~~
The legacy :func:`extract_countermodel` free function and the original
``Countermodel.assignment`` / ``Countermodel.support`` /
``Countermodel.reasons`` attributes are preserved so that
:mod:`jugeo.solver.reconstruction` and other downstream modules continue
to work without changes.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

from jugeo.errors import (
    EvidenceFamily,
    ObstructionRecord,
    RepairHint,
    RepairPriority,
)
from jugeo.evidence.manifests import ObstructionKind
from jugeo.geometry.site import CoordinateObject
from jugeo.geometry.supports import SupportRegion
from jugeo.solver.fragments import LogicalFragment, SolverFragment
from jugeo.solver.z3_session import SolveOutcome, SolverResult

# ---------------------------------------------------------------------------
# Optional imports for cross-subsystem integration (judgment-geometric links)
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment as _Judgment,
        JudgmentBuilder as _JudgmentBuilder,
        Obstruction as _JudgmentObstruction,
        Proposition as _Proposition,
    )
    _JUDGMENT_TERMS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JUDGMENT_TERMS_AVAILABLE = False

try:
    from jugeo.geometry.descent import (
        DescentObstruction as _DescentObstruction,
        LocalSection as _LocalSection,
        OverlapCondition as _OverlapCondition,
    )
    _DESCENT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DESCENT_AVAILABLE = False

try:
    from jugeo.evidence.manifests import (
        EvidenceManifest as _EvidenceManifest,
        ObstructionStore as _ObstructionStore,
        build_evidence_manifest as _build_evidence_manifest,
    )
    from jugeo.evidence.channels import (
        EvidenceRecord as _EvidenceRecord,
        EvidenceChannel as _EvidenceChannel,
    )
    _EVIDENCE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _EVIDENCE_AVAILABLE = False

try:
    from jugeo.problem_modes.repair_semantics import (
        RepairSemanticsIntegration as _RepairSemanticsIntegration,
        compute_minimal_repair_frontier as _compute_minimal_repair_frontier,
        CounterexampleRecord as _CounterexampleRecord,
    )
    _REPAIR_SEMANTICS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REPAIR_SEMANTICS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Failure classification used by ObstructionConverter
# ---------------------------------------------------------------------------


class FailureClass(str, Enum):
    """High-level classification of why a claim failed.

    Used by :class:`ObstructionConverter` to tag the obstruction record
    with a machine-readable failure category that downstream tooling —
    including the copilot repair pipeline — can dispatch on.
    """

    ASSIGNMENT_CONFLICT = "assignment_conflict"
    SORT_VIOLATION = "sort_violation"
    FUNCTION_MISMATCH = "function_mismatch"
    ARRAY_OUT_OF_BOUNDS = "array_out_of_bounds"
    QUANTIFIER_WITNESS = "quantifier_witness"
    UNKNOWN = "unknown"


class RepairType(str, Enum):
    """Taxonomy of repair actions generated from countermodels.

    Each value corresponds to a concrete code-level or specification-level
    action that can resolve the obstruction.
    """

    STRENGTHEN_PRECONDITION = "strengthen_precondition"
    WEAKEN_POSTCONDITION = "weaken_postcondition"
    ADD_INVARIANT = "add_invariant"
    FIX_IMPLEMENTATION = "fix_implementation"
    SPLIT_COVER = "split_cover"
    ADD_SORT_CONSTRAINT = "add_sort_constraint"
    REFINE_FUNCTION_SPEC = "refine_function_spec"
    MANUAL_REVIEW = "manual_review"


# ---------------------------------------------------------------------------
# Countermodel — rich dataclass extending the legacy triple
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Countermodel:
    """A concrete counterexample extracted from a solver model.

    Preserves the legacy ``assignment`` / ``support`` / ``reasons``
    triple for backward compatibility while adding the richer structure
    required by theory2.tex ch11.

    Attributes
    ----------
    assignment : dict[str, bool]
        Legacy propositional assignment map (variable → truth value).
    support : SupportRegion or None
        Geometric support region where the countermodel lives.
    reasons : tuple of str
        Solver-provided reason strings.
    model_id : str
        Unique identifier for this countermodel instance.
    coordinate : str
        The semantic coordinate where the negated claim was checked.
    negated_proposition : str
        The proposition whose negation was found satisfiable.
    variable_assignments : dict[str, Any]
        Full assignment map including non-boolean sorts.
    sort_interpretations : dict[str, tuple[str, ...]]
        For each uninterpreted sort, the concrete domain elements the
        solver chose.
    function_interpretations : dict[str, dict[str, str]]
        For each uninterpreted function, a mapping from stringified
        argument tuples to result values.
    is_minimal : bool
        Whether this countermodel has been minimized.
    extraction_time_ms : float
        Wall-clock time spent extracting from the solver model.
    """

    assignment: dict[str, bool]
    support: SupportRegion | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    model_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    coordinate: str = ""
    negated_proposition: str = ""
    variable_assignments: dict[str, Any] = field(default_factory=dict)
    sort_interpretations: dict[str, tuple[str, ...]] = field(default_factory=dict)
    function_interpretations: dict[str, dict[str, str]] = field(default_factory=dict)
    is_minimal: bool = False
    extraction_time_ms: float = 0.0

    # -- query & projection methods -----------------------------------------

    def evaluate(self, variable: str) -> Any | None:
        """Look up the value of *variable* in the full assignment.

        Falls back to the legacy boolean ``assignment`` map when the
        variable is not present in ``variable_assignments``.

        Parameters
        ----------
        variable:
            Name of the variable to evaluate.

        Returns
        -------
        The value assigned by the solver, or ``None`` if the variable is
        absent from both maps.
        """
        if variable in self.variable_assignments:
            return self.variable_assignments[variable]
        return self.assignment.get(variable)

    def restrict_to_variables(self, variables: set[str]) -> Countermodel:
        """Return a copy restricted to the given variable names.

        Useful for projecting a large countermodel down to the variables
        that actually appear in a specific clause or sub-formula.

        Parameters
        ----------
        variables:
            The set of variable names to keep.

        Returns
        -------
        A new :class:`Countermodel` containing only the requested
        variables.
        """
        restricted_assignment = {
            k: v for k, v in self.assignment.items() if k in variables
        }
        restricted_vars = {
            k: v for k, v in self.variable_assignments.items() if k in variables
        }
        return Countermodel(
            assignment=restricted_assignment,
            support=self.support,
            reasons=self.reasons,
            model_id=f"{self.model_id}:restricted",
            coordinate=self.coordinate,
            negated_proposition=self.negated_proposition,
            variable_assignments=restricted_vars,
            sort_interpretations=dict(self.sort_interpretations),
            function_interpretations=dict(self.function_interpretations),
            is_minimal=False,
            extraction_time_ms=self.extraction_time_ms,
        )

    def project_to_types(self, sort_names: set[str]) -> Countermodel:
        """Project the countermodel onto a subset of uninterpreted sorts.

        Only sort interpretations, function interpretations whose range
        or domain involves the selected sorts, and variables whose type
        (if tracked) belongs to the selected sorts are retained.

        Parameters
        ----------
        sort_names:
            Set of sort names to keep.

        Returns
        -------
        A new :class:`Countermodel` projected onto the selected sorts.
        """
        projected_sorts = {
            k: v for k, v in self.sort_interpretations.items() if k in sort_names
        }
        projected_fns: dict[str, dict[str, str]] = {}
        for fn_name, fn_map in self.function_interpretations.items():
            # Keep function if any of its domain/range sort names appear in
            # sort_names — we approximate by checking element membership.
            all_sort_elements = {
                elem for elems in projected_sorts.values() for elem in elems
            }
            filtered = {
                args: result
                for args, result in fn_map.items()
                if result in all_sort_elements or any(
                    a in all_sort_elements for a in args.split(",")
                )
            }
            if filtered:
                projected_fns[fn_name] = filtered
        return Countermodel(
            assignment=dict(self.assignment),
            support=self.support,
            reasons=self.reasons,
            model_id=f"{self.model_id}:proj",
            coordinate=self.coordinate,
            negated_proposition=self.negated_proposition,
            variable_assignments=dict(self.variable_assignments),
            sort_interpretations=projected_sorts,
            function_interpretations=projected_fns,
            is_minimal=False,
            extraction_time_ms=self.extraction_time_ms,
        )

    def to_test_case(self) -> dict[str, Any]:
        """Convert the countermodel to a serializable test-case dictionary.

        The resulting dictionary is suitable for consumption by
        :class:`TestCaseGenerator` to produce executable unit tests.

        Returns
        -------
        A dictionary containing all assignment data and metadata.
        """
        return {
            "model_id": self.model_id,
            "coordinate": self.coordinate,
            "negated_proposition": self.negated_proposition,
            "assignments": {**self.assignment, **self.variable_assignments},
            "sorts": {k: list(v) for k, v in self.sort_interpretations.items()},
            "functions": dict(self.function_interpretations),
            "is_minimal": self.is_minimal,
        }

    def to_obstruction(self) -> ObstructionRecord:
        """Convenience wrapper — delegates to :class:`ObstructionConverter`.

        Returns
        -------
        An :class:`~jugeo.errors.ObstructionRecord` derived from this
        countermodel.
        """
        converter = ObstructionConverter()
        return converter.to_obstruction_record(self)

    def serialize(self) -> dict[str, Any]:
        """Serialize the countermodel to a JSON-compatible dictionary.

        The result round-trips through :func:`json.dumps` /
        :func:`json.loads` without loss.

        Returns
        -------
        A plain dictionary ready for JSON serialization.
        """
        return {
            "model_id": self.model_id,
            "coordinate": self.coordinate,
            "negated_proposition": self.negated_proposition,
            "assignment": self.assignment,
            "variable_assignments": self.variable_assignments,
            "sort_interpretations": {
                k: list(v) for k, v in self.sort_interpretations.items()
            },
            "function_interpretations": self.function_interpretations,
            "is_minimal": self.is_minimal,
            "extraction_time_ms": self.extraction_time_ms,
            "reasons": list(self.reasons),
        }

    def pretty_print(self, *, indent: int = 2) -> str:
        """Render a human-readable multi-line summary.

        Parameters
        ----------
        indent:
            Number of spaces for nested indentation.

        Returns
        -------
        A formatted string suitable for terminal output.
        """
        pad = " " * indent
        lines = [
            f"Countermodel [{self.model_id}]",
            f"{pad}coordinate  : {self.coordinate or '(unknown)'}",
            f"{pad}proposition : ¬({self.negated_proposition or '?'})",
            f"{pad}minimal     : {self.is_minimal}",
            f"{pad}extraction  : {self.extraction_time_ms:.1f} ms",
        ]
        if self.assignment:
            lines.append(f"{pad}boolean assignments:")
            for var, val in sorted(self.assignment.items()):
                lines.append(f"{pad}{pad}{var} = {val}")
        if self.variable_assignments:
            lines.append(f"{pad}typed assignments:")
            for var, val in sorted(self.variable_assignments.items()):
                lines.append(f"{pad}{pad}{var} = {val!r}")
        if self.sort_interpretations:
            lines.append(f"{pad}sort domains:")
            for sort_name, elems in sorted(self.sort_interpretations.items()):
                lines.append(f"{pad}{pad}{sort_name} = {{{', '.join(elems)}}}")
        if self.function_interpretations:
            lines.append(f"{pad}function tables:")
            for fn_name, fn_map in sorted(self.function_interpretations.items()):
                lines.append(f"{pad}{pad}{fn_name}:")
                for args, result in sorted(fn_map.items()):
                    lines.append(f"{pad}{pad}{pad}({args}) → {result}")
        if self.reasons:
            lines.append(f"{pad}reasons: {'; '.join(self.reasons)}")
        return "\n".join(lines)

    # -- Cross-subsystem integration methods --------------------------------

    def to_judgment_obstruction(self) -> Any:
        """Convert this countermodel into a judgment-geometric obstruction.

        Builds an :class:`~jugeo.judgments.judgment_terms.Obstruction` that
        records the negated proposition, the failing coordinate, and the
        variable assignments as structured evidence that the claim cannot
        hold.  When descent data is available the obstruction also carries
        a :class:`~jugeo.geometry.descent.DescentObstruction` capturing the
        local section where gluing fails.

        Returns
        -------
        A judgment-level obstruction object, or a plain ``dict`` fallback
        when the ``jugeo.judgments.judgment_terms`` module is unavailable.

        Raises
        ------
        RuntimeError
            If the countermodel has neither a coordinate nor a proposition.
        """
        if not self.coordinate and not self.negated_proposition:
            raise RuntimeError(
                "Cannot build a judgment obstruction from a countermodel "
                "that lacks both coordinate and negated_proposition."
            )

        details: dict[str, Any] = {
            "model_id": self.model_id,
            "variable_assignments": {
                **self.assignment,
                **self.variable_assignments,
            },
            "sort_interpretations": {
                k: list(v) for k, v in self.sort_interpretations.items()
            },
            "function_interpretations": dict(self.function_interpretations),
        }

        if _JUDGMENT_TERMS_AVAILABLE:
            proposition = _Proposition(
                kind="semantic",
                formula=self.negated_proposition or "(unknown)",
            )
            obstruction = _JudgmentObstruction(
                coordinate=self.coordinate or "(global)",
                reason=f"Countermodel witness: {self.model_id}",
                details=details,
                proposition=proposition,
            )
        else:
            obstruction = {
                "coordinate": self.coordinate or "(global)",
                "reason": f"Countermodel witness: {self.model_id}",
                "details": details,
                "negated_proposition": self.negated_proposition,
            }

        if _DESCENT_AVAILABLE and self.support is not None:
            try:
                local_section = _LocalSection(
                    coordinate=self.coordinate or "(global)",
                    judgment_data=details,
                    evidence_bundle={},
                    trust_level=0.0,
                    provenance=f"countermodel:{self.model_id}",
                    is_partial=True,
                )
                descent_obs = _DescentObstruction(
                    source=local_section,
                    reason=f"Countermodel refutes claim at {self.coordinate}",
                )
                if isinstance(obstruction, dict):
                    obstruction["descent_obstruction"] = {
                        "coordinate": local_section.coordinate,
                        "reason": descent_obs.reason,
                    }
                else:
                    obstruction = (obstruction, descent_obs)
            except Exception:
                pass  # Descent enrichment is best-effort

        return obstruction

    def evidence_from_countermodel(self) -> Any:
        """Create negative evidence entries from this countermodel.

        Produces an :class:`~jugeo.evidence.channels.EvidenceRecord` with
        channel ``SOLVER`` that records the countermodel's refutation as
        structured negative evidence.  When the manifests subsystem is
        available, an :class:`~jugeo.evidence.manifests.EvidenceManifest`
        is returned wrapping the record with trust and provenance metadata.

        Returns
        -------
        An ``EvidenceManifest`` when the evidence subsystem is available,
        otherwise a plain dictionary describing the negative evidence.
        """
        payload: dict[str, Any] = {
            "polarity": "negative",
            "model_id": self.model_id,
            "coordinate": self.coordinate,
            "negated_proposition": self.negated_proposition,
            "assignments": {**self.assignment, **self.variable_assignments},
            "is_minimal": self.is_minimal,
        }

        if _EVIDENCE_AVAILABLE:
            record = _EvidenceRecord(
                channel=_EvidenceChannel.SOLVER,
                claim=f"¬({self.negated_proposition})" if self.negated_proposition else "(refuted)",
                payload=payload,
                obligations=(),
                provenance=f"countermodel:{self.model_id}",
            )
            try:
                manifest = _build_evidence_manifest(
                    coordinate=self.coordinate or "(global)",
                    claim=record.claim,
                    records=(record,),
                    trust_profiles=(),
                    provenance=None,
                )
                return manifest
            except Exception:
                return record

        return {
            "channel": "solver",
            "claim": f"¬({self.negated_proposition})" if self.negated_proposition else "(refuted)",
            "payload": payload,
            "kind": "negative_evidence",
        }

    def repair_via_semantics(self) -> Any:
        """Obtain repair suggestions based on this countermodel.

        Delegates to :mod:`jugeo.problem_modes.repair_semantics` to compute
        a minimal repair frontier — the smallest set of specification or
        implementation changes that would eliminate this countermodel.

        Returns
        -------
        A repair frontier object when the repair-semantics subsystem is
        available, otherwise a plain dictionary with best-effort hints
        derived from the countermodel's failure classification.
        """
        if _REPAIR_SEMANTICS_AVAILABLE:
            try:
                cx_record = _CounterexampleRecord(
                    model_id=self.model_id,
                    coordinate=self.coordinate,
                    proposition=self.negated_proposition,
                    assignments={**self.assignment, **self.variable_assignments},
                    sort_interpretations={
                        k: list(v) for k, v in self.sort_interpretations.items()
                    },
                    function_interpretations=dict(self.function_interpretations),
                )
                frontier = _compute_minimal_repair_frontier(cx_record)
                return frontier
            except Exception:
                pass  # Fall through to local hints

        # Best-effort local repair hints when the subsystem is unavailable
        hints: list[dict[str, Any]] = []
        if self.sort_interpretations:
            hints.append({
                "action": "add_sort_constraint",
                "reason": "Countermodel uses unexpected sort domains",
                "sorts": list(self.sort_interpretations.keys()),
            })
        if self.function_interpretations:
            hints.append({
                "action": "refine_function_spec",
                "reason": "Function interpretations deviate from intent",
                "functions": list(self.function_interpretations.keys()),
            })
        if not hints:
            hints.append({
                "action": "strengthen_precondition",
                "reason": "Generic repair: tighten input constraints",
            })
        return {
            "model_id": self.model_id,
            "coordinate": self.coordinate,
            "hints": hints,
            "subsystem_available": False,
        }

    # -- Judgment-geometric property API -----------------------------------

    @property
    def as_obstruction(self) -> Any:
        r"""Convert this countermodel to a Čech cohomological obstruction.

        In the sheaf-cohomological framework (Theory2.tex §9.4), a
        countermodel witnesses a non-trivial class in ``H^1(\mathbf{C}, T)``
        — the first Čech cohomology of the trust presheaf.  When ``H^1 \neq 0``
        the descent problem (can compatible local trust assignments glue to a
        global one?) is unsolvable, and this countermodel is the constructive
        content of that obstruction.

        Returns
        -------
        A ``DescentObstruction`` when ``jugeo.geometry.descent`` is available,
        otherwise a plain dict describing the obstruction.
        """
        try:
            from jugeo.geometry.descent import (
                DescentObstruction, LocalSection,
            )
        except ImportError:
            return {
                "kind": "cech_obstruction",
                "model_id": self.model_id,
                "coordinate": self.coordinate,
                "negated_proposition": self.negated_proposition,
                "h1_witness": True,
                "assignments": {**self.assignment, **self.variable_assignments},
            }

        details = {
            "model_id": self.model_id,
            "assignments": {**self.assignment, **self.variable_assignments},
        }
        try:
            return DescentObstruction(
                coordinate=self.coordinate or "(global)",
                partial_section=details,
            )
        except Exception:
            return {
                "kind": "cech_obstruction",
                "model_id": self.model_id,
                "coordinate": self.coordinate,
                "negated_proposition": self.negated_proposition,
                "h1_witness": True,
                "assignments": {**self.assignment, **self.variable_assignments},
            }

    @property
    def judgment_violation(self) -> Any:
        r"""Identify which judgment is violated by this countermodel.

        A countermodel is a section of the negated judgment presheaf: it
        provides a point ``c \in \mathbf{C}`` and an element of
        ``\neg\mathcal{J}(c)`` that witnesses the failure.  This property
        reconstructs the violated judgment from the stored proposition and
        coordinate.

        Returns
        -------
        A ``Judgment`` when ``jugeo.judgments.judgment_terms`` is available,
        otherwise a dict.
        """
        try:
            from jugeo.judgments.judgment_terms import (
                Judgment, Proposition, PropositionKind, Carrier,
            )
            from jugeo.geometry.site import CoordinateObject as _CO
        except ImportError:
            return {
                "kind": "judgment_violation",
                "coordinate": self.coordinate,
                "proposition": self.negated_proposition,
                "model_id": self.model_id,
            }

        try:
            coord = _CO(name=self.coordinate or "(global)")
            prop = Proposition(
                kind=PropositionKind.SEMANTIC,
                formula=self.negated_proposition or "(unknown)",
            )
            carrier = Carrier(name="CountermodelWitness")
            return Judgment(
                coordinate=coord,
                proposition=prop,
                carrier=carrier,
            )
        except Exception:
            return {
                "kind": "judgment_violation",
                "coordinate": self.coordinate,
                "proposition": self.negated_proposition,
                "model_id": self.model_id,
            }

    @property
    def repair_frontier(self) -> Any:
        r"""Compute the minimal repair frontier for this countermodel.

        The *repair frontier* is the smallest set of specification or
        implementation changes that would eliminate this countermodel — it
        is the boundary in spec-space between the current (failing) spec
        and the nearest succeeding one.  Delegates to
        ``jugeo.problem_modes.repair_semantics``.

        Returns
        -------
        A repair frontier object or a dict of best-effort hints.
        """
        try:
            from jugeo.problem_modes.repair_semantics import (
                compute_minimal_repair_frontier,
                CounterexampleRecord,
            )
        except ImportError:
            return {
                "kind": "repair_frontier",
                "model_id": self.model_id,
                "coordinate": self.coordinate,
                "hints": [{"action": "strengthen_precondition"}],
            }

        cx = CounterexampleRecord(
            model_id=self.model_id,
            coordinate=self.coordinate,
            proposition=self.negated_proposition,
            assignments={**self.assignment, **self.variable_assignments},
            sort_interpretations={
                k: list(v) for k, v in self.sort_interpretations.items()
            },
            function_interpretations=dict(self.function_interpretations),
        )
        return compute_minimal_repair_frontier(cx)

    def evidence_record(self) -> Any:
        r"""Create a negative evidence entry for the evidence manifest.

        Negative evidence records that a claim *fails* at a coordinate.
        In the trust presheaf ``T``, negative evidence reduces the trust
        value assigned to the corresponding section, potentially triggering
        a challenge and demotion via ``\downarrow_\chi``.

        Returns
        -------
        An ``EvidenceManifest`` or ``EvidenceRecord``, or a plain dict.
        """
        try:
            from jugeo.evidence.manifests import (
                EvidenceManifest, build_evidence_manifest,
            )
            from jugeo.evidence.channels import EvidenceRecord, EvidenceChannel
        except ImportError:
            return {
                "kind": "negative_evidence",
                "model_id": self.model_id,
                "coordinate": self.coordinate,
                "proposition": self.negated_proposition,
                "channel": "solver",
                "polarity": "negative",
            }

        record = EvidenceRecord(
            channel=EvidenceChannel.SOLVER,
            claim=f"¬({self.negated_proposition})" if self.negated_proposition else "(refuted)",
            payload={
                "polarity": "negative",
                "model_id": self.model_id,
                "assignments": {**self.assignment, **self.variable_assignments},
            },
            obligations=(),
            provenance=f"countermodel:{self.model_id}",
        )
        try:
            return build_evidence_manifest(
                coordinate=self.coordinate or "(global)",
                claim=record.claim,
                records=(record,),
                trust_profiles=(),
                provenance=None,
            )
        except Exception:
            return record

    @property
    def site_location(self) -> Any:
        r"""Return the geometric coordinate where this countermodel localises failure.

        In the judgment site ``(\mathbf{C}, J)`` every countermodel is
        *localised* at a coordinate ``c``.  This property wraps the raw
        coordinate string into a first-class ``CoordinateObject`` from the
        geometry subsystem.

        Returns
        -------
        A ``CoordinateObject`` when available, otherwise a dict.
        """
        try:
            from jugeo.geometry.site import CoordinateObject, Coordinate, CoordinateKind
        except ImportError:
            return {
                "coordinate": self.coordinate,
                "model_id": self.model_id,
                "kind": "countermodel_site",
            }

        return CoordinateObject(
            name=self.coordinate or "(global)",
            kind=CoordinateKind.REGION,
            metadata={"model_id": self.model_id},
        )

    def encode_failure(self) -> Any:
        r"""Encode this countermodel's failure into the scalar-encoding pipeline.

        The encoding translates the countermodel's variable assignments
        and violated proposition into an SMT formula that can be analysed
        by the structural-frontier machinery to determine how far outside
        the decidable interior the failure lies.

        Returns
        -------
        An ``EncodingContext`` or ``RefinementEncoding`` when the encoding
        subsystem is available, otherwise a dict of the SMT-level failure.
        """
        try:
            from jugeo.encodings.scalar_encodings.models import (
                RefinementEncoding, EncodingContext, SortKind,
                FragmentHint, make_encoding_id, make_context_id,
            )
            from jugeo.geometry.supports import SupportRegion
        except ImportError:
            return {
                "kind": "encoded_failure",
                "model_id": self.model_id,
                "proposition": self.negated_proposition,
                "assignments": {**self.assignment, **self.variable_assignments},
            }

        import time as _time
        constraint_parts = []
        for var, val in self.variable_assignments.items():
            constraint_parts.append(f"(= {var} {val})")
        for var, val in self.assignment.items():
            constraint_parts.append(f"(= {var} {'true' if val else 'false'})")

        smt_constraint = (
            f"(and {' '.join(constraint_parts)})"
            if len(constraint_parts) > 1
            else constraint_parts[0] if constraint_parts
            else "(assert true)"
        )

        return RefinementEncoding(
            encoding_id=make_encoding_id(),
            base_sort=SortKind.BOOL,
            predicate_str=f"failure-witness:{self.model_id}",
            z3_constraint_smt=smt_constraint,
            fragment=FragmentHint.QF_LIA,
            support=self.support or SupportRegion(
                coordinate=self.coordinate or "(global)",
            ),
            created_at=_time.time(),
            copilot_suggested=False,
        )


# ---------------------------------------------------------------------------
# CountermodelExtractor — pulls structured data from solver results
# ---------------------------------------------------------------------------


class CountermodelExtractor:
    """Extracts :class:`Countermodel` instances from raw solver results.

    The extractor interprets the flat ``model`` dict returned by the
    solver adapter and populates the richer fields of :class:`Countermodel`
    using naming conventions to infer sorts, functions, and array stores.
    """

    # Naming conventions for sort/function/array detection
    SORT_PREFIX: str = "__sort_"
    FUNC_PREFIX: str = "__fn_"
    ARRAY_PREFIX: str = "__arr_"

    def extract(
        self,
        result: SolverResult,
        *,
        coordinate: str = "",
        negated_proposition: str = "",
        support: SupportRegion | None = None,
    ) -> Countermodel | None:
        """Extract a full countermodel from a solver result.

        Returns ``None`` when the result is not SAT or contains no model
        data.  This subsumes the legacy :func:`extract_countermodel`
        function.

        Parameters
        ----------
        result:
            The raw :class:`SolverResult` from a solver adapter.
        coordinate:
            Semantic coordinate where the check was issued.
        negated_proposition:
            The proposition whose negation was checked.
        support:
            Optional geometric support region.

        Returns
        -------
        A fully-populated :class:`Countermodel`, or ``None``.
        """
        if result.outcome is not SolveOutcome.SAT or not result.model:
            return None

        start = time.monotonic_ns()
        bool_assignment = dict(result.model)
        variable_assignments = self.extract_variables(result)
        sort_interps = self.extract_sorts(result)
        func_interps = self.extract_functions(result)
        array_interps = self.extract_arrays(result)
        # Merge array interpretations into functions under a unified key
        for arr_name, arr_map in array_interps.items():
            func_interps[f"select({arr_name})"] = arr_map
        elapsed_ms = (time.monotonic_ns() - start) / 1_000_000

        return Countermodel(
            assignment=bool_assignment,
            support=support,
            reasons=result.reasons,
            coordinate=coordinate,
            negated_proposition=negated_proposition,
            variable_assignments=variable_assignments,
            sort_interpretations=sort_interps,
            function_interpretations=func_interps,
            is_minimal=False,
            extraction_time_ms=elapsed_ms,
        )

    def extract_variables(self, result: SolverResult) -> dict[str, Any]:
        """Pull typed variable assignments from the raw model.

        Non-boolean entries are detected by their naming prefix or by
        the presence of non-boolean values in the model dict.

        Parameters
        ----------
        result:
            Solver result containing the raw model.

        Returns
        -------
        Mapping of variable names to their assigned values.
        """
        typed: dict[str, Any] = {}
        for name, value in result.model.items():
            if name.startswith(self.SORT_PREFIX) or name.startswith(self.FUNC_PREFIX):
                continue
            if name.startswith(self.ARRAY_PREFIX):
                continue
            typed[name] = value
        return typed

    def extract_sorts(self, result: SolverResult) -> dict[str, tuple[str, ...]]:
        """Identify uninterpreted-sort domains from the model.

        Sort entries are recognized by the ``__sort_`` prefix and decoded
        as comma-separated domain element lists.

        Parameters
        ----------
        result:
            Solver result containing the raw model.

        Returns
        -------
        Mapping from sort name to its concrete domain elements.
        """
        sorts: dict[str, tuple[str, ...]] = {}
        for name, value in result.model.items():
            if name.startswith(self.SORT_PREFIX):
                sort_name = name[len(self.SORT_PREFIX):]
                if isinstance(value, str):
                    sorts[sort_name] = tuple(
                        elem.strip() for elem in value.split(",") if elem.strip()
                    )
                elif isinstance(value, bool):
                    sorts[sort_name] = (str(value),)
                else:
                    sorts[sort_name] = (str(value),)
        return sorts

    def extract_functions(self, result: SolverResult) -> dict[str, dict[str, str]]:
        """Recover function interpretation tables from the model.

        Function entries follow the ``__fn_<name>_<args>`` naming
        convention. The argument tuple is reconstructed from the suffix.

        Parameters
        ----------
        result:
            Solver result containing the raw model.

        Returns
        -------
        Mapping from function name to argument-result table.
        """
        funcs: dict[str, dict[str, str]] = defaultdict(dict)
        for name, value in result.model.items():
            if not name.startswith(self.FUNC_PREFIX):
                continue
            remainder = name[len(self.FUNC_PREFIX):]
            parts = remainder.split("_", 1)
            fn_name = parts[0]
            args_key = parts[1] if len(parts) > 1 else "()"
            funcs[fn_name][args_key] = str(value)
        return dict(funcs)

    def extract_arrays(self, result: SolverResult) -> dict[str, dict[str, str]]:
        """Recover array store/select interpretations from the model.

        Array entries follow the ``__arr_<name>_<index>`` convention.

        Parameters
        ----------
        result:
            Solver result containing the raw model.

        Returns
        -------
        Mapping from array name to index-value table.
        """
        arrays: dict[str, dict[str, str]] = defaultdict(dict)
        for name, value in result.model.items():
            if not name.startswith(self.ARRAY_PREFIX):
                continue
            remainder = name[len(self.ARRAY_PREFIX):]
            parts = remainder.split("_", 1)
            arr_name = parts[0]
            idx_key = parts[1] if len(parts) > 1 else "0"
            arrays[arr_name][idx_key] = str(value)
        return dict(arrays)

    def minimize(self, countermodel: Countermodel, checker: SolverFragment) -> Countermodel:
        """Attempt to reduce the countermodel using a delta-debugging pass.

        Delegates to :class:`CountermodelMinimizer` for the heavy
        lifting.

        Parameters
        ----------
        countermodel:
            The countermodel to minimize.
        checker:
            Fragment used to re-check minimality.

        Returns
        -------
        A minimized countermodel.
        """
        minimizer = CountermodelMinimizer()
        return minimizer.minimize(countermodel, checker)

    def is_genuine(self, countermodel: Countermodel, fragment: SolverFragment) -> bool:
        """Verify that the countermodel is a genuine satisfying assignment.

        Evaluates each clause in *fragment* against the countermodel's
        assignment and returns ``True`` only when every clause is
        satisfied.

        Parameters
        ----------
        countermodel:
            The countermodel to verify.
        fragment:
            The solver fragment the model should satisfy.

        Returns
        -------
        ``True`` if every clause evaluates to ``True`` under the
        assignment; ``False`` otherwise.
        """
        for clause in fragment.clauses:
            stripped = clause.strip()
            if not stripped:
                continue
            negated = stripped.startswith("not ")
            atom = stripped[4:].strip() if negated else stripped
            value = countermodel.evaluate(atom)
            if value is None:
                # Variable missing — cannot confirm genuineness.
                return False
            truth = bool(value) if not negated else not bool(value)
            if not truth:
                return False
        return True


# ---------------------------------------------------------------------------
# CountermodelMinimizer — shrinks countermodels to essential cores
# ---------------------------------------------------------------------------


class CountermodelMinimizer:
    """Minimizes countermodels by removing irrelevant assignments.

    Implements delta-debugging and iterative core extraction to produce
    a *minimal* countermodel — one where removing any single assignment
    makes it no longer a valid counterexample.
    """

    MAX_DELTA_ITERATIONS: int = 50

    def minimize(self, countermodel: Countermodel, fragment: SolverFragment) -> Countermodel:
        """Run the full minimization pipeline.

        Applies irrelevant-assignment removal, then delta-debugging to
        reach a minimal core.

        Parameters
        ----------
        countermodel:
            The countermodel to minimize.
        fragment:
            The original solver fragment used for re-checking.

        Returns
        -------
        A new :class:`Countermodel` marked ``is_minimal=True``.
        """
        reduced = self.remove_irrelevant_assignments(countermodel, fragment)
        minimal = self.delta_debug(reduced, fragment)
        minimal.is_minimal = True
        return minimal

    def remove_irrelevant_assignments(
        self, countermodel: Countermodel, fragment: SolverFragment
    ) -> Countermodel:
        """Drop variables not mentioned in any clause.

        A quick first pass that eliminates obviously irrelevant
        variables without expensive solver re-checks.

        Parameters
        ----------
        countermodel:
            The countermodel to prune.
        fragment:
            The fragment defining which variables are relevant.

        Returns
        -------
        A pruned :class:`Countermodel`.
        """
        mentioned: set[str] = set()
        for clause in fragment.clauses:
            tokens = clause.replace("not ", "").split()
            mentioned.update(tok.strip() for tok in tokens if tok.strip())
        return countermodel.restrict_to_variables(mentioned)

    def find_minimal_core(
        self, countermodel: Countermodel, fragment: SolverFragment
    ) -> set[str]:
        """Identify the minimal set of variables required for the countermodel.

        Uses an iterative removal strategy: for each variable, attempt
        to remove it and check whether the remaining assignment still
        satisfies the negated formula.

        Parameters
        ----------
        countermodel:
            The countermodel to analyze.
        fragment:
            Fragment used for satisfaction checks.

        Returns
        -------
        The minimal set of variable names forming the core.
        """
        core_vars = set(countermodel.assignment.keys()) | set(
            countermodel.variable_assignments.keys()
        )
        for var in list(core_vars):
            candidate = core_vars - {var}
            restricted = countermodel.restrict_to_variables(candidate)
            if self.is_still_counter(restricted, fragment):
                core_vars = candidate
        return core_vars

    def delta_debug(
        self, countermodel: Countermodel, fragment: SolverFragment
    ) -> Countermodel:
        """Apply delta-debugging to shrink the countermodel.

        Splits the assignment into halves and recursively attempts to
        reduce, bounded by :attr:`MAX_DELTA_ITERATIONS`.

        Parameters
        ----------
        countermodel:
            The countermodel to shrink.
        fragment:
            Fragment used for re-validation.

        Returns
        -------
        The smallest countermodel found within the iteration budget.
        """
        all_vars = sorted(
            set(countermodel.assignment.keys())
            | set(countermodel.variable_assignments.keys())
        )
        if len(all_vars) <= 1:
            return countermodel

        granularity = 2
        iterations = 0
        current = countermodel
        current_vars = list(all_vars)

        while granularity <= len(current_vars) and iterations < self.MAX_DELTA_ITERATIONS:
            chunk_size = max(1, len(current_vars) // granularity)
            chunks = [
                current_vars[i : i + chunk_size]
                for i in range(0, len(current_vars), chunk_size)
            ]
            reduced = False
            for chunk in chunks:
                candidate_vars = set(current_vars) - set(chunk)
                if not candidate_vars:
                    continue
                candidate = current.restrict_to_variables(candidate_vars)
                if self.is_still_counter(candidate, fragment):
                    current = candidate
                    current_vars = sorted(candidate_vars)
                    granularity = max(2, granularity - 1)
                    reduced = True
                    break
                iterations += 1
            if not reduced:
                if granularity >= len(current_vars):
                    break
                granularity *= 2
        return current

    def is_still_counter(
        self, countermodel: Countermodel, fragment: SolverFragment
    ) -> bool:
        """Check whether *countermodel* still satisfies the negated formula.

        Re-evaluates each clause against the restricted assignment.
        Returns ``True`` when the restricted assignment still makes the
        formula satisfiable (i.e. is still a valid counterexample).

        Parameters
        ----------
        countermodel:
            The (possibly restricted) countermodel to check.
        fragment:
            Fragment containing clauses to evaluate.

        Returns
        -------
        ``True`` if the countermodel remains a valid counterexample.
        """
        extractor = CountermodelExtractor()
        return extractor.is_genuine(countermodel, fragment)


# ---------------------------------------------------------------------------
# CountermodelNormalizer — canonical forms for comparison
# ---------------------------------------------------------------------------


class CountermodelNormalizer:
    """Brings countermodels into canonical form for cross-run comparison.

    Two countermodels that are structurally identical but use different
    witness names or element orderings should compare as equal after
    normalization.
    """

    WITNESS_PREFIX: str = "w"

    def normalize(self, countermodel: Countermodel) -> Countermodel:
        """Apply the full normalization pipeline.

        Renames witnesses, collapses equivalent elements, and sorts all
        internal structures deterministically.

        Parameters
        ----------
        countermodel:
            The countermodel to normalize.

        Returns
        -------
        A canonical :class:`Countermodel`.
        """
        renamed = self.rename_witnesses(countermodel)
        collapsed = self.collapse_equivalent(renamed)
        return collapsed

    def canonical_form(self, countermodel: Countermodel) -> dict[str, Any]:
        """Compute a JSON-serializable canonical form.

        The canonical form uses sorted keys, normalized witness names,
        and deterministic element ordering so that two structurally
        equivalent countermodels produce byte-identical canonical forms.

        Parameters
        ----------
        countermodel:
            The countermodel to canonicalize.

        Returns
        -------
        A canonical dictionary suitable for hashing or comparison.
        """
        normalized = self.normalize(countermodel)
        form: dict[str, Any] = {
            "assignment": dict(sorted(normalized.assignment.items())),
            "variable_assignments": dict(
                sorted(
                    (k, v)
                    for k, v in normalized.variable_assignments.items()
                )
            ),
            "sort_interpretations": {
                k: sorted(v)
                for k, v in sorted(normalized.sort_interpretations.items())
            },
            "function_interpretations": {
                fn: dict(sorted(table.items()))
                for fn, table in sorted(
                    normalized.function_interpretations.items()
                )
            },
        }
        return form

    def rename_witnesses(self, countermodel: Countermodel) -> Countermodel:
        """Rename solver-generated witness names to canonical labels.

        Witnesses (elements of uninterpreted sorts) are renamed to
        ``w0``, ``w1``, … in order of first occurrence.

        Parameters
        ----------
        countermodel:
            The countermodel to rename.

        Returns
        -------
        A :class:`Countermodel` with canonical witness names.
        """
        # Build a mapping from old witness name → canonical name
        rename_map: dict[str, str] = {}
        counter = 0

        # Process sort elements in sorted order for determinism
        for sort_name in sorted(countermodel.sort_interpretations):
            for elem in countermodel.sort_interpretations[sort_name]:
                if elem not in rename_map:
                    rename_map[elem] = f"{self.WITNESS_PREFIX}{counter}"
                    counter += 1

        # Rename sort interpretations
        new_sorts: dict[str, tuple[str, ...]] = {}
        for sort_name in sorted(countermodel.sort_interpretations):
            new_sorts[sort_name] = tuple(
                rename_map.get(e, e)
                for e in countermodel.sort_interpretations[sort_name]
            )

        # Rename function interpretations
        new_funcs: dict[str, dict[str, str]] = {}
        for fn_name in sorted(countermodel.function_interpretations):
            old_table = countermodel.function_interpretations[fn_name]
            new_table: dict[str, str] = {}
            for args, result in sorted(old_table.items()):
                new_args = ",".join(
                    rename_map.get(a.strip(), a.strip())
                    for a in args.split(",")
                )
                new_table[new_args] = rename_map.get(result, result)
            new_funcs[fn_name] = new_table

        # Rename variable assignments that reference sort elements
        new_var_assigns: dict[str, Any] = {}
        for var, val in sorted(countermodel.variable_assignments.items()):
            str_val = str(val)
            new_var_assigns[var] = rename_map.get(str_val, val)

        return Countermodel(
            assignment=dict(sorted(countermodel.assignment.items())),
            support=countermodel.support,
            reasons=countermodel.reasons,
            model_id=countermodel.model_id,
            coordinate=countermodel.coordinate,
            negated_proposition=countermodel.negated_proposition,
            variable_assignments=new_var_assigns,
            sort_interpretations=new_sorts,
            function_interpretations=new_funcs,
            is_minimal=countermodel.is_minimal,
            extraction_time_ms=countermodel.extraction_time_ms,
        )

    def collapse_equivalent(self, countermodel: Countermodel) -> Countermodel:
        """Merge sort elements that are treated identically everywhere.

        Two domain elements are *equivalent* when they appear in exactly
        the same positions across all function tables.  Collapsing them
        reduces the countermodel size without changing its semantic
        content.

        Parameters
        ----------
        countermodel:
            The countermodel to collapse.

        Returns
        -------
        A :class:`Countermodel` with equivalent elements merged.
        """
        # Build a fingerprint for each sort element: the set of
        # (function_name, arg_position_or_result, context) tuples it
        # appears in.
        fingerprints: dict[str, frozenset[tuple[str, ...]]] = {}
        for fn_name, fn_table in countermodel.function_interpretations.items():
            for args_str, result in fn_table.items():
                arg_list = [a.strip() for a in args_str.split(",")]
                for i, arg in enumerate(arg_list):
                    key = (fn_name, f"arg{i}", args_str, result)
                    fingerprints.setdefault(arg, set()).add(key)  # type: ignore[arg-type]
                result_key = (fn_name, "result", args_str)
                fingerprints.setdefault(result, set()).add(result_key)  # type: ignore[arg-type]

        # Freeze fingerprints for comparison
        frozen_fps: dict[str, frozenset[tuple[str, ...]]] = {
            elem: frozenset(fp) if isinstance(fp, set) else fp
            for elem, fp in fingerprints.items()
        }

        # Group elements by fingerprint
        groups: dict[frozenset[tuple[str, ...]], list[str]] = defaultdict(list)
        for elem, fp in frozen_fps.items():
            groups[fp].append(elem)

        # Build a merge map: keep the first element of each group
        merge_map: dict[str, str] = {}
        for group_elems in groups.values():
            canonical = sorted(group_elems)[0]
            for elem in group_elems:
                merge_map[elem] = canonical

        # If nothing to merge, return as-is
        if all(k == v for k, v in merge_map.items()):
            return countermodel

        # Apply the merge map
        new_sorts: dict[str, tuple[str, ...]] = {}
        for sn, elems in countermodel.sort_interpretations.items():
            seen: set[str] = set()
            merged: list[str] = []
            for e in elems:
                canonical = merge_map.get(e, e)
                if canonical not in seen:
                    seen.add(canonical)
                    merged.append(canonical)
            new_sorts[sn] = tuple(merged)

        new_funcs: dict[str, dict[str, str]] = {}
        for fn_name, fn_table in countermodel.function_interpretations.items():
            new_table: dict[str, str] = {}
            for args_str, result in fn_table.items():
                new_args = ",".join(
                    merge_map.get(a.strip(), a.strip())
                    for a in args_str.split(",")
                )
                new_result = merge_map.get(result, result)
                new_table[new_args] = new_result
            new_funcs[fn_name] = new_table

        return Countermodel(
            assignment=countermodel.assignment,
            support=countermodel.support,
            reasons=countermodel.reasons,
            model_id=countermodel.model_id,
            coordinate=countermodel.coordinate,
            negated_proposition=countermodel.negated_proposition,
            variable_assignments=countermodel.variable_assignments,
            sort_interpretations=new_sorts,
            function_interpretations=new_funcs,
            is_minimal=countermodel.is_minimal,
            extraction_time_ms=countermodel.extraction_time_ms,
        )

    def hash_normalized(self, countermodel: Countermodel) -> str:
        """Compute a stable SHA-256 digest of the canonical form.

        Two structurally equivalent countermodels (modulo witness naming
        and element ordering) always produce the same hash.

        Parameters
        ----------
        countermodel:
            The countermodel to hash.

        Returns
        -------
        A hex-encoded SHA-256 digest string.
        """
        canonical = self.canonical_form(countermodel)
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# ObstructionConverter — countermodel → theory2 obstruction record
# ---------------------------------------------------------------------------


class ObstructionConverter:
    """Converts countermodels into first-class JuGeo obstruction records.

    Theory2.tex defines an obstruction as the formal tuple
    ``(c, κ, E, R, Δ)`` — coordinate, violated condition, evidence,
    repair frontier, and downstream effects.  This converter populates
    each component from the information available in the countermodel.
    """

    def to_obstruction_record(self, countermodel: Countermodel) -> ObstructionRecord:
        """Build a full :class:`~jugeo.errors.ObstructionRecord`.

        Parameters
        ----------
        countermodel:
            The countermodel to convert.

        Returns
        -------
        A populated :class:`ObstructionRecord`.
        """
        violated = self.extract_violated_condition(countermodel)
        hints = self.compute_repair_hints(countermodel)
        failure = self.classify_failure(countermodel)
        cost = self.estimate_repair_cost(countermodel)

        support_scope: tuple[str, ...] = ()
        if countermodel.support is not None:
            support_scope = tuple(countermodel.support.patch_keys)

        provenance: dict[str, Any] = {
            "source": "countermodel",
            "model_id": countermodel.model_id,
            "failure_class": failure.value,
            "estimated_cost": cost,
            "extraction_time_ms": countermodel.extraction_time_ms,
        }

        return ObstructionRecord(
            coordinate=countermodel.coordinate,
            violated_condition=violated,
            evidence_family=EvidenceFamily.SOLVER,
            evidence={"countermodel": countermodel.serialize()},
            repair_hints=hints,
            downstream_obligations=(),
            support_scope=support_scope,
            provenance=provenance,
            is_coboundary=self._is_trivially_resolvable(countermodel),
        )

    def extract_violated_condition(self, countermodel: Countermodel) -> str:
        """Determine which admissibility condition was violated.

        Analyzes the countermodel structure to identify the kind of
        violation — for instance, a sort with an empty domain indicates
        a sort inhabitedness violation, while conflicting boolean
        assignments indicate a logical inconsistency in the claim.

        Parameters
        ----------
        countermodel:
            The countermodel to analyze.

        Returns
        -------
        A human-readable condition string.
        """
        # Check for empty sorts (sort inhabitedness violation)
        for sort_name, elems in countermodel.sort_interpretations.items():
            if not elems:
                return f"sort_inhabitedness({sort_name})"

        # Check for conflicting boolean assignments
        true_vars = {v for v, b in countermodel.assignment.items() if b}
        false_vars = {v for v, b in countermodel.assignment.items() if not b}
        if true_vars & false_vars:
            overlap = true_vars & false_vars
            return f"consistency({', '.join(sorted(overlap))})"

        if countermodel.negated_proposition:
            return f"claim({countermodel.negated_proposition})"

        return "unspecified_admissibility"

    def compute_repair_hints(self, countermodel: Countermodel) -> tuple[RepairHint, ...]:
        """Generate concrete repair hints from the countermodel.

        Delegates to :class:`RepairHintGenerator` for the actual hint
        production.

        Parameters
        ----------
        countermodel:
            The countermodel to derive repair hints from.

        Returns
        -------
        A tuple of :class:`~jugeo.errors.RepairHint` values, ordered
        by priority.
        """
        generator = RepairHintGenerator()
        hints = generator.suggest_repairs(countermodel)
        return tuple(generator.rank_by_cost(hints))

    def estimate_repair_cost(self, countermodel: Countermodel) -> str:
        """Estimate how much effort is needed to fix the obstruction.

        Parameters
        ----------
        countermodel:
            The countermodel to assess.

        Returns
        -------
        One of ``"trivial"``, ``"moderate"``, or ``"significant"``.
        """
        total_assignments = len(countermodel.assignment) + len(
            countermodel.variable_assignments
        )
        total_functions = sum(
            len(table)
            for table in countermodel.function_interpretations.values()
        )
        complexity = total_assignments + total_functions * 2

        if complexity <= 3:
            return "trivial"
        elif complexity <= 10:
            return "moderate"
        return "significant"

    def classify_failure(self, countermodel: Countermodel) -> FailureClass:
        """Classify the failure mode exhibited by the countermodel.

        Parameters
        ----------
        countermodel:
            The countermodel to classify.

        Returns
        -------
        A :class:`FailureClass` value.
        """
        if countermodel.sort_interpretations:
            for _sort_name, elems in countermodel.sort_interpretations.items():
                if not elems:
                    return FailureClass.SORT_VIOLATION

        if countermodel.function_interpretations:
            return FailureClass.FUNCTION_MISMATCH

        has_true = any(v for v in countermodel.assignment.values())
        has_false = any(not v for v in countermodel.assignment.values())
        if has_true and has_false:
            return FailureClass.ASSIGNMENT_CONFLICT

        return FailureClass.UNKNOWN

    def _is_trivially_resolvable(self, countermodel: Countermodel) -> bool | None:
        """Heuristic for coboundary detection.

        A countermodel is considered trivially resolvable (a coboundary
        in the cohomological sense) when it involves at most one
        variable and no function interpretations.

        Parameters
        ----------
        countermodel:
            The countermodel to evaluate.

        Returns
        -------
        ``True``, ``False``, or ``None`` if undecidable.
        """
        total_vars = len(countermodel.assignment) + len(
            countermodel.variable_assignments
        )
        if total_vars <= 1 and not countermodel.function_interpretations:
            return True
        if total_vars > 5 or len(countermodel.function_interpretations) > 2:
            return False
        return None


# ---------------------------------------------------------------------------
# TestCaseGenerator — countermodels become executable tests
# ---------------------------------------------------------------------------


class TestCaseGenerator:
    """Generates executable test cases from countermodels.

    When a countermodel is discovered it encodes a concrete input that
    violates the claim.  This generator turns that concrete input into
    unit tests, property tests, and regression tests so that the same
    violation is caught by the test suite in future verification cycles.
    """

    DEFAULT_FIXTURE_NAME: str = "countermodel_fixture"

    def to_unit_test(self, countermodel: Countermodel, *, test_name: str = "") -> str:
        """Generate a pytest-style unit test string.

        Parameters
        ----------
        countermodel:
            The countermodel to encode.
        test_name:
            Optional custom test name; a default is generated from the
            model id.

        Returns
        -------
        A Python source string defining a single test function.
        """
        name = test_name or f"test_countermodel_{countermodel.model_id}"
        data = countermodel.to_test_case()
        lines = [
            f"def {name}():",
            f'    """Regression test from countermodel {countermodel.model_id}.',
            f"",
            f"    Negated proposition: {countermodel.negated_proposition}",
            f"    Coordinate: {countermodel.coordinate}",
            f'    """',
        ]
        # Emit variable assignments as setup
        for var, val in sorted(data["assignments"].items()):
            lines.append(f"    {var} = {val!r}")

        # Emit assertion that the negated proposition holds (i.e. the
        # original claim fails under these assignments)
        if countermodel.negated_proposition:
            lines.append(f"    # The original claim '{countermodel.negated_proposition}' should fail")
            lines.append(f"    # under these assignments — this test documents the failure.")

        # Final assertion placeholder
        lines.append(f"    assert {name}_check({', '.join(sorted(data['assignments'].keys()))})")
        lines.append("")
        return "\n".join(lines)

    def to_property_test(self, countermodel: Countermodel) -> str:
        """Generate a Hypothesis-style property test skeleton.

        The countermodel's sort domains are used to derive
        ``st.sampled_from`` strategies, and the function tables inform
        the property body.

        Parameters
        ----------
        countermodel:
            The countermodel to encode.

        Returns
        -------
        A Python source string defining a property-based test.
        """
        lines = [
            "from hypothesis import given, settings",
            "import hypothesis.strategies as st",
            "",
        ]
        # Build strategies from sort domains
        strategies: list[str] = []
        for sort_name, elems in sorted(countermodel.sort_interpretations.items()):
            elems_repr = ", ".join(repr(e) for e in elems)
            strategies.append(f"{sort_name}=st.sampled_from([{elems_repr}])")

        # Add boolean strategies for each boolean variable
        for var in sorted(countermodel.assignment.keys()):
            strategies.append(f"{var}=st.booleans()")

        decorator = f"@given({', '.join(strategies)})" if strategies else "@given()"
        lines.append(decorator)
        lines.append(f"@settings(max_examples=200)")
        params = ", ".join(
            list(sorted(countermodel.sort_interpretations.keys()))
            + sorted(countermodel.assignment.keys())
        )
        lines.append(f"def test_property_{countermodel.model_id}({params}):")
        lines.append(f'    """Property test derived from countermodel {countermodel.model_id}."""')
        lines.append(f"    # Verify that the claim holds for all inputs,")
        lines.append(f"    # not just the specific counterexample.")
        lines.append(f"    pass  # TODO: encode claim as assertion")
        lines.append("")
        return "\n".join(lines)

    def to_regression_test(self, countermodel: Countermodel) -> str:
        """Generate a regression test that pins the exact counterexample.

        Unlike :meth:`to_unit_test`, this variant includes the expected
        outputs from function tables, making it a stricter regression
        check.

        Parameters
        ----------
        countermodel:
            The countermodel to encode.

        Returns
        -------
        A Python source string defining a regression test.
        """
        name = f"test_regression_{countermodel.model_id}"
        lines = [
            f"def {name}():",
            f'    """Exact regression test — pins countermodel {countermodel.model_id}."""',
        ]
        # Pin boolean assignments
        for var, val in sorted(countermodel.assignment.items()):
            lines.append(f"    assert {var!r} == {val!r}  # pinned")
        # Pin function tables
        for fn_name, fn_table in sorted(countermodel.function_interpretations.items()):
            lines.append(f"    # Function {fn_name}:")
            for args, result in sorted(fn_table.items()):
                lines.append(f"    assert lookup_{fn_name}({args!r}) == {result!r}")
        lines.append("")
        return "\n".join(lines)

    def parameterize(self, countermodels: Sequence[Countermodel]) -> str:
        """Generate a ``pytest.mark.parametrize``-decorated test.

        Combines multiple countermodels into a single parametrized test
        so that the full countermodel corpus is checked in one function.

        Parameters
        ----------
        countermodels:
            Sequence of countermodels to parameterize over.

        Returns
        -------
        A Python source string with parametrized test.
        """
        if not countermodels:
            return "# No countermodels to parameterize.\n"

        # Collect all variable names across countermodels
        all_vars: set[str] = set()
        for cm in countermodels:
            all_vars.update(cm.assignment.keys())
            all_vars.update(cm.variable_assignments.keys())
        sorted_vars = sorted(all_vars)

        # Build parameter rows
        rows: list[str] = []
        for cm in countermodels:
            vals: list[str] = []
            merged = {**cm.assignment, **cm.variable_assignments}
            for var in sorted_vars:
                vals.append(repr(merged.get(var)))
            rows.append(f"    ({', '.join(vals)}),  # {cm.model_id}")

        params_str = ", ".join(f'"{v}"' for v in sorted_vars)
        lines = [
            "import pytest",
            "",
            f"@pytest.mark.parametrize(",
            f'    "{", ".join(sorted_vars)}",',
            "    [",
        ]
        lines.extend(rows)
        lines.append("    ],")
        lines.append(")")
        lines.append(f"def test_countermodels({', '.join(sorted_vars)}):")
        lines.append(f'    """Parametrized regression over {len(countermodels)} countermodels."""')
        lines.append(f"    pass  # TODO: encode claim check")
        lines.append("")
        return "\n".join(lines)

    def generate_fixtures(self, countermodels: Sequence[Countermodel]) -> str:
        """Generate a pytest conftest.py snippet with fixtures.

        Each countermodel becomes a fixture that downstream tests can
        request by name.

        Parameters
        ----------
        countermodels:
            Countermodels to convert into fixtures.

        Returns
        -------
        A Python source string defining pytest fixtures.
        """
        lines = [
            "import pytest",
            "",
        ]
        for cm in countermodels:
            fixture_name = f"{self.DEFAULT_FIXTURE_NAME}_{cm.model_id}"
            lines.append("@pytest.fixture")
            lines.append(f"def {fixture_name}():")
            lines.append(f'    """Fixture for countermodel {cm.model_id}."""')
            lines.append(f"    return {cm.to_test_case()!r}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CountermodelExplainer — human-readable narratives
# ---------------------------------------------------------------------------


class CountermodelExplainer:
    """Produces human-readable explanations of countermodels.

    Explanations target developers who may not be familiar with SMT
    internals.  The :meth:`copilot_explanation` method produces a
    structured explanation suitable for consumption by the copilot
    orchestration layer, which can present it in IDE hover panels or
    inline diagnostics.
    """

    def explain(self, countermodel: Countermodel) -> str:
        """Generate a complete natural-language explanation.

        Parameters
        ----------
        countermodel:
            The countermodel to explain.

        Returns
        -------
        A multi-paragraph explanation string.
        """
        sections: list[str] = []
        sections.append(self._explain_header(countermodel))
        if countermodel.assignment or countermodel.variable_assignments:
            sections.append(self._explain_assignments(countermodel))
        if countermodel.sort_interpretations:
            sections.append(self._explain_sorts(countermodel))
        if countermodel.function_interpretations:
            sections.append(self._explain_functions(countermodel))
        sections.append(self._explain_conclusion(countermodel))
        return "\n\n".join(sections)

    def explain_variable(self, countermodel: Countermodel, variable: str) -> str:
        """Explain what a specific variable's assignment means.

        Parameters
        ----------
        countermodel:
            The countermodel containing the variable.
        variable:
            Name of the variable to explain.

        Returns
        -------
        A single-sentence explanation.
        """
        value = countermodel.evaluate(variable)
        if value is None:
            return f"Variable '{variable}' is not present in this countermodel."
        if isinstance(value, bool):
            truth_word = "true" if value else "false"
            return (
                f"The solver set '{variable}' to {truth_word}, meaning the "
                f"corresponding condition {'holds' if value else 'does not hold'} "
                f"in this counterexample."
            )
        return (
            f"The solver assigned '{variable}' the value {value!r} — a concrete "
            f"witness from the domain that makes the negated claim satisfiable."
        )

    def explain_violation(self, countermodel: Countermodel) -> str:
        """Explain *why* the countermodel constitutes a violation.

        Focuses on the relationship between the countermodel and the
        original claim.

        Parameters
        ----------
        countermodel:
            The countermodel to explain.

        Returns
        -------
        An explanation of the violation.
        """
        if not countermodel.negated_proposition:
            return (
                "A countermodel was found, but the original proposition "
                "is not recorded.  The solver found a satisfying "
                "assignment for the negation of some claim."
            )
        return (
            f"The claim '{countermodel.negated_proposition}' was negated and "
            f"sent to the solver.  The solver found a satisfying assignment "
            f"(a countermodel), which means the original claim does NOT hold "
            f"at coordinate '{countermodel.coordinate or '(unknown)'}' under "
            f"the given variable assignments."
        )

    def generate_narrative(self, countermodel: Countermodel) -> str:
        """Build a step-by-step narrative of how the countermodel arose.

        Parameters
        ----------
        countermodel:
            The countermodel to narrate.

        Returns
        -------
        A numbered narrative.
        """
        steps: list[str] = []
        steps.append(
            f"1. The claim '{countermodel.negated_proposition or '?'}' was "
            f"submitted for verification at coordinate "
            f"'{countermodel.coordinate or '(unknown)'}'."
        )
        steps.append(
            "2. The solver negated the claim and searched for a satisfying "
            "assignment."
        )
        steps.append(
            f"3. After {countermodel.extraction_time_ms:.1f} ms the solver "
            f"returned SAT with {len(countermodel.assignment)} boolean "
            f"assignment(s) and {len(countermodel.variable_assignments)} "
            f"typed assignment(s)."
        )
        if countermodel.is_minimal:
            steps.append(
                "4. The countermodel was minimized — no assignment can be "
                "removed without invalidating the counterexample."
            )
        else:
            steps.append(
                "4. The countermodel has NOT been minimized and may contain "
                "redundant assignments."
            )
        steps.append(
            "5. The countermodel has been converted to an obstruction record "
            "with repair hints for the development team."
        )
        return "\n".join(steps)

    def copilot_explanation(self, countermodel: Countermodel) -> dict[str, Any]:
        """Produce a structured explanation for the copilot layer.

        Returns a dictionary that the copilot orchestration engine can
        consume to render IDE-friendly diagnostics — hover information,
        inline annotations, and quickfix suggestions.

        Parameters
        ----------
        countermodel:
            The countermodel to explain.

        Returns
        -------
        A structured explanation dictionary with keys ``summary``,
        ``details``, ``violation``, ``narrative``, and ``variables``.
        """
        variable_explanations: dict[str, str] = {}
        for var in sorted(
            set(countermodel.assignment.keys())
            | set(countermodel.variable_assignments.keys())
        ):
            variable_explanations[var] = self.explain_variable(countermodel, var)

        return {
            "summary": self._explain_header(countermodel),
            "details": self.explain(countermodel),
            "violation": self.explain_violation(countermodel),
            "narrative": self.generate_narrative(countermodel),
            "variables": variable_explanations,
            "model_id": countermodel.model_id,
            "coordinate": countermodel.coordinate,
            "is_minimal": countermodel.is_minimal,
        }

    # -- private helpers ----------------------------------------------------

    def _explain_header(self, countermodel: Countermodel) -> str:
        """One-line summary."""
        n_vars = len(countermodel.assignment) + len(countermodel.variable_assignments)
        minimal_tag = " (minimal)" if countermodel.is_minimal else ""
        return (
            f"Countermodel {countermodel.model_id}{minimal_tag}: "
            f"{n_vars} variable(s) at '{countermodel.coordinate or '?'}'."
        )

    def _explain_assignments(self, countermodel: Countermodel) -> str:
        """Paragraph about variable assignments."""
        lines = ["Variable assignments in this countermodel:"]
        for var, val in sorted(countermodel.assignment.items()):
            lines.append(f"  • {var} = {val}")
        for var, val in sorted(countermodel.variable_assignments.items()):
            lines.append(f"  • {var} = {val!r}")
        return "\n".join(lines)

    def _explain_sorts(self, countermodel: Countermodel) -> str:
        """Paragraph about sort interpretations."""
        lines = ["The solver chose concrete domains for uninterpreted sorts:"]
        for sort_name, elems in sorted(countermodel.sort_interpretations.items()):
            lines.append(f"  • {sort_name} = {{{', '.join(elems)}}}")
        return "\n".join(lines)

    def _explain_functions(self, countermodel: Countermodel) -> str:
        """Paragraph about function interpretations."""
        lines = ["Function interpretations in this countermodel:"]
        for fn_name, fn_table in sorted(countermodel.function_interpretations.items()):
            lines.append(f"  • {fn_name}:")
            for args, result in sorted(fn_table.items()):
                lines.append(f"      ({args}) → {result}")
        return "\n".join(lines)

    def _explain_conclusion(self, countermodel: Countermodel) -> str:
        """Closing paragraph."""
        return (
            "This countermodel is a concrete witness that the claim fails.  "
            "Use the repair hints attached to the corresponding obstruction "
            "record to guide your fix, or ask the copilot layer for a "
            "suggested code change."
        )


# ---------------------------------------------------------------------------
# CountermodelStore — persistence layer
# ---------------------------------------------------------------------------


class CountermodelStore:
    """In-memory store for countermodels with query capabilities.

    Provides persistence-like semantics (store, retrieve, query, prune)
    backed by a dictionary.  A future iteration may delegate to SQLite
    or the manifest store.
    """

    def __init__(self) -> None:
        self._models: dict[str, Countermodel] = {}
        self._by_coordinate: dict[str, list[str]] = defaultdict(list)
        self._by_proposition: dict[str, list[str]] = defaultdict(list)
        self._stored_at: dict[str, float] = {}

    def store(self, countermodel: Countermodel) -> str:
        """Persist a countermodel and return its id.

        Parameters
        ----------
        countermodel:
            The countermodel to store.

        Returns
        -------
        The ``model_id`` under which the countermodel was stored.
        """
        mid = countermodel.model_id
        self._models[mid] = countermodel
        self._by_coordinate[countermodel.coordinate].append(mid)
        self._by_proposition[countermodel.negated_proposition].append(mid)
        self._stored_at[mid] = time.monotonic()
        return mid

    def retrieve(self, model_id: str) -> Countermodel | None:
        """Retrieve a countermodel by its id.

        Parameters
        ----------
        model_id:
            The unique identifier.

        Returns
        -------
        The countermodel, or ``None`` if not found.
        """
        return self._models.get(model_id)

    def query_by_coordinate(self, coordinate: str) -> list[Countermodel]:
        """Return all countermodels at a given coordinate.

        Parameters
        ----------
        coordinate:
            Semantic coordinate to filter by.

        Returns
        -------
        List of matching countermodels, newest first.
        """
        ids = self._by_coordinate.get(coordinate, [])
        return [self._models[mid] for mid in reversed(ids) if mid in self._models]

    def query_by_proposition(self, proposition: str) -> list[Countermodel]:
        """Return all countermodels for a given proposition.

        Parameters
        ----------
        proposition:
            The negated proposition to filter by.

        Returns
        -------
        List of matching countermodels.
        """
        ids = self._by_proposition.get(proposition, [])
        return [self._models[mid] for mid in reversed(ids) if mid in self._models]

    def prune_old(self, max_age_seconds: float) -> int:
        """Remove countermodels older than *max_age_seconds*.

        Parameters
        ----------
        max_age_seconds:
            Maximum age in seconds.

        Returns
        -------
        The number of countermodels pruned.
        """
        now = time.monotonic()
        to_remove: list[str] = [
            mid
            for mid, stored_time in self._stored_at.items()
            if (now - stored_time) > max_age_seconds
        ]
        for mid in to_remove:
            self._models.pop(mid, None)
            self._stored_at.pop(mid, None)
        # Clean up indices
        for coord in list(self._by_coordinate):
            self._by_coordinate[coord] = [
                mid for mid in self._by_coordinate[coord] if mid in self._models
            ]
            if not self._by_coordinate[coord]:
                del self._by_coordinate[coord]
        for prop in list(self._by_proposition):
            self._by_proposition[prop] = [
                mid for mid in self._by_proposition[prop] if mid in self._models
            ]
            if not self._by_proposition[prop]:
                del self._by_proposition[prop]
        return len(to_remove)

    def statistics(self) -> dict[str, Any]:
        """Return summary statistics about the stored countermodels.

        Returns
        -------
        A dictionary with counts and breakdowns.
        """
        total = len(self._models)
        minimal_count = sum(1 for cm in self._models.values() if cm.is_minimal)
        coordinates = len(self._by_coordinate)
        propositions = len(self._by_proposition)
        avg_vars = (
            sum(
                len(cm.assignment) + len(cm.variable_assignments)
                for cm in self._models.values()
            )
            / max(total, 1)
        )
        return {
            "total_countermodels": total,
            "minimal_countermodels": minimal_count,
            "distinct_coordinates": coordinates,
            "distinct_propositions": propositions,
            "average_variables": round(avg_vars, 2),
        }


# ---------------------------------------------------------------------------
# CountermodelComparator — structural comparison
# ---------------------------------------------------------------------------


class CountermodelComparator:
    """Compares countermodels for equivalence, refinement, and difference.

    Uses :class:`CountermodelNormalizer` internally so that comparison
    is insensitive to witness naming and element ordering.
    """

    def __init__(self) -> None:
        self._normalizer = CountermodelNormalizer()

    def is_equivalent(self, a: Countermodel, b: Countermodel) -> bool:
        """Test whether two countermodels are structurally equivalent.

        Equivalence is defined modulo witness renaming and element
        reordering.

        Parameters
        ----------
        a, b:
            The countermodels to compare.

        Returns
        -------
        ``True`` if the countermodels are equivalent.
        """
        return self._normalizer.hash_normalized(a) == self._normalizer.hash_normalized(b)

    def is_refinement(self, candidate: Countermodel, base: Countermodel) -> bool:
        """Test whether *candidate* is a refinement (subset) of *base*.

        A countermodel refines another when every assignment in the
        candidate also appears in the base with the same value.

        Parameters
        ----------
        candidate:
            The potential refinement.
        base:
            The base countermodel.

        Returns
        -------
        ``True`` if *candidate* refines *base*.
        """
        for var, val in candidate.assignment.items():
            if var not in base.assignment or base.assignment[var] != val:
                return False
        for var, val in candidate.variable_assignments.items():
            if var not in base.variable_assignments:
                return False
            if base.variable_assignments[var] != val:
                return False
        return True

    def diff(self, a: Countermodel, b: Countermodel) -> dict[str, Any]:
        """Compute a structured diff between two countermodels.

        Reports variables that are only in one side, variables with
        differing values, and sort / function differences.

        Parameters
        ----------
        a, b:
            The countermodels to diff.

        Returns
        -------
        A dictionary with keys ``only_in_a``, ``only_in_b``,
        ``differing_values``, ``sort_diffs``, ``function_diffs``.
        """
        all_bool_vars = set(a.assignment.keys()) | set(b.assignment.keys())
        all_typed_vars = set(a.variable_assignments.keys()) | set(
            b.variable_assignments.keys()
        )

        only_a: dict[str, Any] = {}
        only_b: dict[str, Any] = {}
        differing: dict[str, dict[str, Any]] = {}

        for var in all_bool_vars:
            in_a = var in a.assignment
            in_b = var in b.assignment
            if in_a and not in_b:
                only_a[var] = a.assignment[var]
            elif in_b and not in_a:
                only_b[var] = b.assignment[var]
            elif a.assignment[var] != b.assignment[var]:
                differing[var] = {"a": a.assignment[var], "b": b.assignment[var]}

        for var in all_typed_vars:
            in_a = var in a.variable_assignments
            in_b = var in b.variable_assignments
            if in_a and not in_b:
                only_a[var] = a.variable_assignments[var]
            elif in_b and not in_a:
                only_b[var] = b.variable_assignments[var]
            elif a.variable_assignments[var] != b.variable_assignments[var]:
                differing[var] = {
                    "a": a.variable_assignments[var],
                    "b": b.variable_assignments[var],
                }

        # Sort domain diffs
        sort_diffs: dict[str, dict[str, Any]] = {}
        all_sorts = set(a.sort_interpretations.keys()) | set(
            b.sort_interpretations.keys()
        )
        for sn in all_sorts:
            a_elems = set(a.sort_interpretations.get(sn, ()))
            b_elems = set(b.sort_interpretations.get(sn, ()))
            if a_elems != b_elems:
                sort_diffs[sn] = {
                    "only_in_a": sorted(a_elems - b_elems),
                    "only_in_b": sorted(b_elems - a_elems),
                }

        # Function table diffs
        func_diffs: dict[str, dict[str, Any]] = {}
        all_fns = set(a.function_interpretations.keys()) | set(
            b.function_interpretations.keys()
        )
        for fn in all_fns:
            a_table = a.function_interpretations.get(fn, {})
            b_table = b.function_interpretations.get(fn, {})
            if a_table != b_table:
                func_diffs[fn] = {"a": a_table, "b": b_table}

        return {
            "only_in_a": only_a,
            "only_in_b": only_b,
            "differing_values": differing,
            "sort_diffs": sort_diffs,
            "function_diffs": func_diffs,
        }

    def common_core(self, a: Countermodel, b: Countermodel) -> Countermodel:
        """Extract the assignments common to both countermodels.

        Returns a new countermodel containing only the variables and
        values that agree in both inputs.

        Parameters
        ----------
        a, b:
            The countermodels to intersect.

        Returns
        -------
        A :class:`Countermodel` with the common assignments.
        """
        common_bool: dict[str, bool] = {}
        for var in set(a.assignment.keys()) & set(b.assignment.keys()):
            if a.assignment[var] == b.assignment[var]:
                common_bool[var] = a.assignment[var]

        common_typed: dict[str, Any] = {}
        for var in set(a.variable_assignments.keys()) & set(
            b.variable_assignments.keys()
        ):
            if a.variable_assignments[var] == b.variable_assignments[var]:
                common_typed[var] = a.variable_assignments[var]

        return Countermodel(
            assignment=common_bool,
            support=a.support if a.support == b.support else None,
            reasons=tuple(set(a.reasons) & set(b.reasons)),
            model_id=f"core({a.model_id},{b.model_id})",
            coordinate=a.coordinate if a.coordinate == b.coordinate else "",
            negated_proposition=(
                a.negated_proposition
                if a.negated_proposition == b.negated_proposition
                else ""
            ),
            variable_assignments=common_typed,
            sort_interpretations={},
            function_interpretations={},
            is_minimal=False,
            extraction_time_ms=0.0,
        )

    def merge_countermodels(
        self, countermodels: Sequence[Countermodel]
    ) -> Countermodel | None:
        """Merge multiple countermodels into a single combined model.

        The merge retains assignments that are consistent across all
        inputs.  Conflicting assignments are dropped.  This is useful
        for synthesizing a "consensus" countermodel from multiple solver
        runs.

        Parameters
        ----------
        countermodels:
            The countermodels to merge.

        Returns
        -------
        A merged :class:`Countermodel`, or ``None`` if the input is
        empty.
        """
        if not countermodels:
            return None
        if len(countermodels) == 1:
            return countermodels[0]

        # Start from all variables in the first countermodel
        merged_bool: dict[str, bool] = dict(countermodels[0].assignment)
        merged_typed: dict[str, Any] = dict(countermodels[0].variable_assignments)

        for cm in countermodels[1:]:
            # Keep only variables that agree
            merged_bool = {
                k: v
                for k, v in merged_bool.items()
                if k in cm.assignment and cm.assignment[k] == v
            }
            merged_typed = {
                k: v
                for k, v in merged_typed.items()
                if k in cm.variable_assignments
                and cm.variable_assignments[k] == v
            }

        ids = ",".join(cm.model_id for cm in countermodels)
        return Countermodel(
            assignment=merged_bool,
            variable_assignments=merged_typed,
            model_id=f"merged({ids})",
            coordinate=countermodels[0].coordinate,
            negated_proposition=countermodels[0].negated_proposition,
            is_minimal=False,
            extraction_time_ms=0.0,
        )


# ---------------------------------------------------------------------------
# RepairHintGenerator — actionable fix suggestions
# ---------------------------------------------------------------------------


class RepairHintGenerator:
    """Generates ranked repair suggestions from countermodels.

    Repair hints are first-class objects in JuGeo (theory2.tex §11.4)
    and flow into the obstruction record's repair frontier.  The
    :meth:`copilot_suggest_fix` method produces a structured suggestion
    that the copilot orchestration layer can present as an inline
    code-action in the IDE.
    """

    def suggest_repairs(self, countermodel: Countermodel) -> list[RepairHint]:
        """Generate a list of repair hints for the given countermodel.

        The hints are derived heuristically from the countermodel
        structure: sort violations suggest adding constraints, function
        mismatches suggest refining specs, and simple boolean conflicts
        suggest strengthening preconditions or weakening postconditions.

        Parameters
        ----------
        countermodel:
            The countermodel to generate repairs for.

        Returns
        -------
        An unranked list of :class:`~jugeo.errors.RepairHint` values.
        """
        hints: list[RepairHint] = []

        # Hint for each boolean variable set to an unexpected value
        for var, val in countermodel.assignment.items():
            if not val:
                hints.append(
                    RepairHint(
                        action="strengthen_precondition",
                        description=(
                            f"Variable '{var}' is False in the countermodel. "
                            f"Consider adding a precondition that requires "
                            f"'{var}' to be True."
                        ),
                        priority=RepairPriority.SUGGESTED,
                        target_coordinate=countermodel.coordinate or None,
                        estimated_effort="trivial",
                    )
                )

        # Hint for sort inhabitedness violations
        for sort_name, elems in countermodel.sort_interpretations.items():
            if not elems:
                hints.append(
                    RepairHint(
                        action="add_sort_constraint",
                        description=(
                            f"Sort '{sort_name}' has an empty domain. Add an "
                            f"inhabitedness axiom or existential witness."
                        ),
                        priority=RepairPriority.RECOMMENDED,
                        target_coordinate=countermodel.coordinate or None,
                        estimated_effort="moderate",
                    )
                )

        # Hint for function interpretation mismatches
        for fn_name in countermodel.function_interpretations:
            hints.append(
                RepairHint(
                    action="refine_function_spec",
                    description=(
                        f"Function '{fn_name}' has an unexpected interpretation "
                        f"in the countermodel. Refine its specification or add "
                        f"constraining axioms."
                    ),
                    priority=RepairPriority.SUGGESTED,
                    target_coordinate=countermodel.coordinate or None,
                    estimated_effort="moderate",
                )
            )

        # General hint if no specific hints were generated
        if not hints:
            hints.append(
                RepairHint(
                    action="manual_review",
                    description=(
                        "No specific repair could be inferred automatically. "
                        "Review the countermodel manually or ask the copilot "
                        "layer for assistance."
                    ),
                    priority=RepairPriority.INFORMATIONAL,
                    target_coordinate=countermodel.coordinate or None,
                    estimated_effort="significant",
                )
            )

        return hints

    def rank_by_cost(self, hints: list[RepairHint]) -> list[RepairHint]:
        """Sort hints by estimated cost (cheapest first), then priority.

        Parameters
        ----------
        hints:
            The unranked hint list.

        Returns
        -------
        A sorted list of hints.
        """
        cost_order = {"trivial": 0, "moderate": 1, "significant": 2, None: 3}
        return sorted(
            hints,
            key=lambda h: (
                cost_order.get(h.estimated_effort, 3),
                -h.priority.value,
            ),
        )

    def classify_repair_type(self, hint: RepairHint) -> RepairType:
        """Map a :class:`~jugeo.errors.RepairHint` to a :class:`RepairType`.

        Parameters
        ----------
        hint:
            The hint to classify.

        Returns
        -------
        The corresponding :class:`RepairType`.
        """
        mapping: dict[str, RepairType] = {
            "strengthen_precondition": RepairType.STRENGTHEN_PRECONDITION,
            "weaken_postcondition": RepairType.WEAKEN_POSTCONDITION,
            "add_invariant": RepairType.ADD_INVARIANT,
            "fix_implementation": RepairType.FIX_IMPLEMENTATION,
            "split_cover": RepairType.SPLIT_COVER,
            "add_sort_constraint": RepairType.ADD_SORT_CONSTRAINT,
            "refine_function_spec": RepairType.REFINE_FUNCTION_SPEC,
            "manual_review": RepairType.MANUAL_REVIEW,
        }
        return mapping.get(hint.action, RepairType.MANUAL_REVIEW)

    def copilot_suggest_fix(self, countermodel: Countermodel) -> dict[str, Any]:
        """Produce a structured fix suggestion for the copilot layer.

        The result is a dictionary that the copilot orchestration engine
        can consume to render an inline code-action — for example a
        "Quick Fix" or "Suggested Change" in the IDE.

        Parameters
        ----------
        countermodel:
            The countermodel to generate a fix for.

        Returns
        -------
        A dictionary with keys ``title``, ``description``, ``hints``,
        ``repair_types``, and ``estimated_total_cost``.
        """
        hints = self.suggest_repairs(countermodel)
        ranked = self.rank_by_cost(hints)
        repair_types = [self.classify_repair_type(h).value for h in ranked]

        # Estimate total cost
        cost_values = {"trivial": 1, "moderate": 3, "significant": 7}
        total_cost = sum(
            cost_values.get(h.estimated_effort or "significant", 7)
            for h in ranked
        )

        return {
            "title": f"Fix countermodel {countermodel.model_id}",
            "description": (
                f"The claim '{countermodel.negated_proposition}' fails at "
                f"'{countermodel.coordinate}'.  {len(ranked)} repair(s) "
                f"suggested."
            ),
            "hints": [h.to_dict() for h in ranked],
            "repair_types": repair_types,
            "estimated_total_cost": total_cost,
            "coordinate": countermodel.coordinate,
            "model_id": countermodel.model_id,
        }

    def validate_hint(
        self, hint: RepairHint, countermodel: Countermodel
    ) -> bool:
        """Check that a repair hint is consistent with its countermodel.

        A hint is valid when its target coordinate matches the
        countermodel's coordinate (if both are set) and the action is
        a recognized repair type.

        Parameters
        ----------
        hint:
            The hint to validate.
        countermodel:
            The countermodel the hint was generated from.

        Returns
        -------
        ``True`` if the hint is consistent.
        """
        if hint.target_coordinate and countermodel.coordinate:
            if hint.target_coordinate != countermodel.coordinate:
                return False
        try:
            self.classify_repair_type(hint)
        except Exception:
            return False
        return True


# ---------------------------------------------------------------------------
# Legacy compatibility shim
# ---------------------------------------------------------------------------


def extract_countermodel(
    result: SolverResult, *, support: SupportRegion | None = None
) -> Countermodel | None:
    """Legacy extraction function — delegates to :class:`CountermodelExtractor`.

    Preserves the original API so that :mod:`jugeo.solver.reconstruction`
    and other call sites continue to work without changes.

    Parameters
    ----------
    result:
        The raw solver result.
    support:
        Optional geometric support region.

    Returns
    -------
    A :class:`Countermodel`, or ``None`` if the result is not SAT.
    """
    extractor = CountermodelExtractor()
    return extractor.extract(result, support=support)


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # Core dataclass
    "Countermodel",
    # Enums
    "FailureClass",
    "RepairType",
    # Extraction & minimization
    "CountermodelExtractor",
    "CountermodelMinimizer",
    # Normalization & comparison
    "CountermodelNormalizer",
    "CountermodelComparator",
    # Conversion & generation
    "ObstructionConverter",
    "TestCaseGenerator",
    # Explanation
    "CountermodelExplainer",
    # Storage
    "CountermodelStore",
    # Repair
    "RepairHintGenerator",
    # Legacy shim
    "extract_countermodel",
]

# copilot: shared-core marker — countermodel extraction, normalization,
# obstruction conversion, and repair hint generation for LLM-assisted
# debugging and proposal sweeps (theory2.tex ch11).
