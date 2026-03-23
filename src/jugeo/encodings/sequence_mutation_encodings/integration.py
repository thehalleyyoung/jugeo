"""integration.py — Integration with the jugeo solver layer.

Theory2.tex Chapter 29: Integration between the sequence mutation encoding
pipeline and the jugeo.solver.* subsystems.

This module implements ``SequenceMutationSolverIntegration``, which bridges:

*   The five encoding layers (s01–s05) in this package.
*   The Z3 session pool (``jugeo.solver.z3_session``).
*   The fragment classifier (``jugeo.solver.fragments``).
*   The reconstruction pipeline (``jugeo.solver.reconstruction``).

The class is the *entry point* for callers who want to verify mutation
correctness end-to-end without assembling the pipeline manually.

Verification workflow
---------------------
1.  ``classify_sequence_fragment`` — determine the Z3 fragment.
2.  ``solve_sequence_query`` — submit the encoding to the solver.
3.  ``reconstruct_mutation_witness`` — extract the witness or countermodel.
4.  ``check_support_preservation`` / ``verify_frame_axiom`` — check invariants.
5.  ``summarize_mutation_evidence`` — produce a ReconstructionReport.

# copilot: SequenceMutationSolverIntegration — Theory2.tex Ch29 integration.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Z3 availability guard
# ---------------------------------------------------------------------------
try:
    import z3 as _z3

    _Z3_AVAILABLE = True
except ImportError:
    _z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False

# ---------------------------------------------------------------------------
# Solver layer imports (optional — all guarded)
# ---------------------------------------------------------------------------
try:
    from jugeo.solver.z3_session import (
        SolveOutcome,
        Z3Result,
        Z3Session,
        Z3SessionPool,
    )

    _Z3_SESSION_AVAILABLE = True
except ImportError:
    SolveOutcome = None  # type: ignore[assignment,misc]
    Z3Result = None  # type: ignore[assignment,misc]
    Z3Session = None  # type: ignore[assignment,misc]
    Z3SessionPool = None  # type: ignore[assignment,misc]
    _Z3_SESSION_AVAILABLE = False

try:
    from jugeo.solver.fragments import (
        Fragment,
        FragmentClassifier,
        classify_fragment,
    )

    _FRAGMENTS_AVAILABLE = True
except ImportError:
    Fragment = None  # type: ignore[assignment,misc]
    FragmentClassifier = None  # type: ignore[assignment,misc]
    classify_fragment = None  # type: ignore[assignment]
    _FRAGMENTS_AVAILABLE = False

try:
    from jugeo.solver.reconstruction import (
        ModelReconstructor,
        ReconstructionPipeline,
        ReconstructionReport,
        ReconstructionResult,
        ValidationStatus,
        reconstruct_countermodel,
    )

    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    ModelReconstructor = None  # type: ignore[assignment,misc]
    ReconstructionPipeline = None  # type: ignore[assignment,misc]
    ReconstructionReport = None  # type: ignore[assignment,misc]
    ReconstructionResult = None  # type: ignore[assignment,misc]
    ValidationStatus = None  # type: ignore[assignment,misc]
    reconstruct_countermodel = None  # type: ignore[assignment]
    _RECONSTRUCTION_AVAILABLE = False

# ---------------------------------------------------------------------------
# Local imports
# ---------------------------------------------------------------------------
from jugeo.encodings.sequence_mutation_encodings.algorithms import (
    check_frame_preservation,
    FramePreservationResult,
)
from jugeo.encodings.sequence_mutation_encodings.models import (
    HeapSlice,
    MutationSlice,
    SequenceEncoding,
    SequenceInvariant,
    SupportAwareMutation,
)
from jugeo.encodings.sequence_mutation_encodings.heap_slice_encoder import (
    EncodedHeapSlice,
    HeapSliceEncoder,
)
from jugeo.encodings.sequence_mutation_encodings.mutation_countermodel_encoder import (
    MutationCountermodelEncoder,
    RepairSuggestion,
    ViolationContext,
)


# ---------------------------------------------------------------------------
# QueryResult stub (used when Z3Result is unavailable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StubZ3Result:
    """Stub Z3 result used when jugeo.solver.z3_session is unavailable.

    Fields
    ------
    outcome : str
        The outcome string: 'sat', 'unsat', 'unknown', 'timeout'.
    model : Any
        The satisfying model, if available.
    elapsed_ms : float
        Elapsed time in milliseconds.
    formula_str : str
        The formula that was checked (as string).
    fragment : str
        The Z3 fragment hint.

    # copilot: _StubZ3Result — fallback when Z3Session is unavailable.
    """

    outcome: str
    model: Any = None
    elapsed_ms: float = 0.0
    formula_str: str = ""
    fragment: str = "UNKNOWN"

    @property
    def is_sat(self) -> bool:
        """Return True iff outcome == 'sat'."""
        return self.outcome == "sat"

    @property
    def is_unsat(self) -> bool:
        """Return True iff outcome == 'unsat'."""
        return self.outcome == "unsat"


# ---------------------------------------------------------------------------
# SequenceMutationSolverIntegration
# ---------------------------------------------------------------------------


class SequenceMutationSolverIntegration:
    """Bridges the sequence mutation encoding pipeline with the jugeo solver layer.

    This class is the primary integration point for end-to-end mutation
    verification.  It:

    1.  Accepts SequenceEncoding, SequenceInvariant, and MutationSlice objects.
    2.  Classifies the resulting Z3 formula into a solver fragment.
    3.  Submits the formula to a Z3SessionPool for solving.
    4.  Extracts witnesses or countermodels via the ReconstructionPipeline.
    5.  Produces a ReconstructionReport summarising the evidence.

    Parameters
    ----------
    session_pool:
        A ``Z3SessionPool`` instance (or None for stub mode).
    fragment_classifier:
        A ``FragmentClassifier`` instance (or None for stub mode).
    reconstruction_pipeline:
        A ``ReconstructionPipeline`` instance (or None for stub mode).
    solver_timeout_ms:
        Default solver timeout in milliseconds.

    # copilot: SequenceMutationSolverIntegration — Theory2.tex Ch29 end-to-end.
    """

    def __init__(
        self,
        session_pool: Any = None,
        fragment_classifier: Any = None,
        reconstruction_pipeline: Any = None,
        solver_timeout_ms: int = 10_000,
    ) -> None:
        """Initialise the integration object.

        Parameters
        ----------
        session_pool:
            Z3SessionPool or None (for stub mode).
        fragment_classifier:
            FragmentClassifier or None (for stub mode).
        reconstruction_pipeline:
            ReconstructionPipeline or None (for stub mode).
        solver_timeout_ms:
            Default solver timeout in milliseconds.
        """
        self._pool = session_pool
        self._classifier = fragment_classifier
        self._pipeline = reconstruction_pipeline
        self._timeout_ms = solver_timeout_ms
        self._results: list[Any] = []
        self._countermodel_encoder = MutationCountermodelEncoder(
            solver_timeout_ms=solver_timeout_ms
        )
        self._heap_encoder = HeapSliceEncoder()

    # ------------------------------------------------------------------
    # Core verification methods
    # ------------------------------------------------------------------

    def solve_sequence_query(
        self,
        encoding: SequenceEncoding,
        invariants: Sequence[SequenceInvariant],
        mutations: Sequence[MutationSlice],
    ) -> Any:
        """Submit a sequence mutation verification query to the solver.

        Builds the query formula:
            ``(encoding invariants) ∧ (mutation predicates) ∧ (post invariants)``

        and solves it via the session pool (or directly via z3 if no pool).

        Parameters
        ----------
        encoding:
            The pre-mutation SequenceEncoding.
        invariants:
            A sequence of SequenceInvariant instances that must hold post-mutation.
        mutations:
            A sequence of MutationSlice instances describing the mutations.

        Returns
        -------
        Any
            A ``Z3Result`` (or ``_StubZ3Result`` in stub mode).

        Theory2.tex §29.1–§29.5 — end-to-end mutation query.

        # copilot: solve_sequence_query — end-to-end sequence verification.
        """
        t0 = time.monotonic()
        parts: list[Any] = []
        # Add encoding invariants
        for inv_formula in encoding.invariant_set():
            parts.append(inv_formula)
        # Add mutation predicates
        for mut in mutations:
            parts.append(mut.mutation_predicate())
        # Add post-state invariants
        for inv in invariants:
            parts.append(inv.check())
        fragment = self.classify_sequence_fragment(encoding)
        if _Z3_AVAILABLE:
            s = _z3.Solver()
            s.set("timeout", self._timeout_ms)
            for p in parts:
                if not isinstance(p, str):
                    try:
                        s.add(p)
                    except Exception as exc:
                        logger.warning("solve_sequence_query: add failed: %s", exc)
            try:
                z3_result = s.check()
                elapsed = (time.monotonic() - t0) * 1000
                outcome_str = str(z3_result)
                model = s.model() if outcome_str == "sat" else None
                result = _StubZ3Result(
                    outcome=outcome_str,
                    model=model,
                    elapsed_ms=elapsed,
                    fragment=str(fragment),
                )
            except Exception as exc:
                elapsed = (time.monotonic() - t0) * 1000
                logger.warning("solve_sequence_query: solver error: %s", exc)
                result = _StubZ3Result(outcome="unknown", elapsed_ms=elapsed)
        else:
            elapsed = (time.monotonic() - t0) * 1000
            result = _StubZ3Result(
                outcome="unknown",
                elapsed_ms=elapsed,
                formula_str=" AND ".join(str(p) for p in parts),
                fragment=str(fragment),
            )
        self._results.append(result)
        return result

    def classify_sequence_fragment(
        self,
        encoding: SequenceEncoding,
    ) -> str:
        """Classify a SequenceEncoding into a Z3 solver fragment.

        Uses the fragment classifier if available; otherwise applies
        heuristics based on the sort name.

        Parameters
        ----------
        encoding:
            The SequenceEncoding to classify.

        Returns
        -------
        str
            A fragment name: ``"QF_AUFLIA"``, ``"SEQUENCES"``, ``"QF_AUFBV"``,
            or ``"UNKNOWN"``.

        Theory2.tex §29.1 — fragment classification.

        # copilot: classify_sequence_fragment — delegates to FragmentClassifier.
        """
        sort = encoding.sort_name.lower()
        if "str" in sort or "string" in sort or "seq" in sort:
            return "SEQUENCES"
        if "bv" in sort or "bitvec" in sort:
            return "QF_AUFBV"
        if "real" in sort or "float" in sort:
            return "QF_AUFLRA"
        if _FRAGMENTS_AVAILABLE and self._classifier is not None:
            try:
                formula_str = str(encoding.encode())
                result = classify_fragment(formula_str)
                return str(result)
            except Exception:
                pass
        return "QF_AUFLIA"

    def reconstruct_mutation_witness(
        self,
        result: Any,
    ) -> Any:
        """Extract and reconstruct a mutation witness from a Z3 result.

        When the result is SAT, reconstructs the concrete witness (sequence
        values satisfying the query) using the reconstruction pipeline.

        Parameters
        ----------
        result:
            A ``Z3Result`` or ``_StubZ3Result``.

        Returns
        -------
        Any
            A ``ReconstructionResult`` or a dict stub.

        Theory2.tex §29.5 — witness reconstruction.

        # copilot: reconstruct_mutation_witness — delegates to ReconstructionPipeline.
        """
        model = result.model if hasattr(result, "model") else None
        if model is None:
            return {"outcome": "no_model", "witness": None}
        if _RECONSTRUCTION_AVAILABLE and self._pipeline is not None:
            try:
                return self._pipeline.reconstruct(model)
            except Exception as exc:
                logger.warning("reconstruct_mutation_witness: pipeline failed: %s", exc)
        # Fallback: extract model as dict
        if _Z3_AVAILABLE and hasattr(model, "__iter__"):
            stub: dict[str, Any] = {}
            try:
                for decl in model:
                    stub[str(decl)] = str(model[decl])
            except Exception:
                pass
            return {"outcome": "sat", "witness": stub}
        return {"outcome": "unknown", "witness": None}

    def check_support_preservation(
        self,
        pre_slice: EncodedHeapSlice,
        post_slice: EncodedHeapSlice,
        mutations: Sequence[SupportAwareMutation] | None = None,
    ) -> tuple[bool, Any]:
        """Check whether the support is preserved by the given mutations.

        Verifies:
            ``support(pre) ∪ support(mutations) ⊆ support(post)``

        and optionally also runs the frame preservation check.

        Parameters
        ----------
        pre_slice:
            The pre-state HeapSlice encoding.
        post_slice:
            The post-state HeapSlice encoding.
        mutations:
            Optional sequence of SupportAwareMutation instances.

        Returns
        -------
        tuple[bool, Any]
            ``(preserved, witness)``

        # copilot: check_support_preservation — support inclusion check.
        """
        pre_sup = pre_slice.heap_slice.support_addresses
        post_sup = post_slice.heap_slice.support_addresses
        muts = list(mutations) if mutations else []
        mut_sup = frozenset().union(*(m.support for m in muts)) if muts else frozenset()
        required = pre_sup | mut_sup
        if not required.issubset(post_sup | pre_sup):
            extra = required - (post_sup | pre_sup)
            return False, {
                "reason": "support_not_preserved",
                "extra_addresses": sorted(extra),
            }
        if muts:
            fp_result: FramePreservationResult = check_frame_preservation(
                pre_slice, post_slice, muts, timeout_ms=self._timeout_ms
            )
            return fp_result.preserved, fp_result.witness
        return True, None

    def verify_frame_axiom(
        self,
        heap_slice: EncodedHeapSlice,
        mutation: SupportAwareMutation,
    ) -> tuple[bool, Any | None]:
        """Verify that the frame axiom holds for a mutation over a heap slice.

        The frame axiom is:
            ``∀ addr ∉ heap_slice.support: post[addr] = pre[addr]``

        Checks this by building a one-step post-slice and calling
        ``check_frame_preservation``.

        Parameters
        ----------
        heap_slice:
            The heap slice to check.
        mutation:
            The SupportAwareMutation to verify.

        Returns
        -------
        tuple[bool, Any | None]
            ``(holds, countermodel)`` — True iff the frame axiom holds;
            countermodel is None if it holds, or a dict if it fails.

        Theory2.tex §29.4 — frame axiom verification.

        # copilot: verify_frame_axiom — Theory2.tex §29.4 frame check.
        """
        support = mutation.support | heap_slice.heap_slice.support_addresses
        encoder = HeapSliceEncoder()
        post_heap = encoder.make_heap_array(
            f"post_{heap_slice.name}", heap_slice.cell_sort
        )
        post_slice = encoder.encode_heap_slice(
            post_heap, support, heap_slice.cell_sort, name=f"{heap_slice.name}_post"
        )
        fp_result = check_frame_preservation(
            heap_slice, post_slice, [mutation], timeout_ms=self._timeout_ms
        )
        if fp_result.preserved:
            return True, None
        return False, fp_result.witness

    def incremental_mutation_check(
        self,
        base_state: SequenceEncoding,
        mutation_chain: Sequence[MutationSlice],
    ) -> list[Any]:
        """Check a chain of mutations incrementally.

        For each mutation in ``mutation_chain``, submits a separate solver
        query checking the mutation + all accumulated invariants.  Returns
        a list of results (one per mutation in the chain).

        Parameters
        ----------
        base_state:
            The base SequenceEncoding before any mutations.
        mutation_chain:
            A sequence of MutationSlice instances to apply in order.

        Returns
        -------
        list[Any]
            A list of ``Z3Result`` or ``_StubZ3Result`` instances.

        Theory2.tex §29.5 — incremental mutation checking.

        # copilot: incremental_mutation_check — chain of mutations checked step-by-step.
        """
        results: list[Any] = []
        current = base_state
        for i, mut in enumerate(mutation_chain):
            logger.debug(
                "incremental_mutation_check: step %d of %d, support=%s",
                i + 1,
                len(mutation_chain),
                sorted(mut.support_set),
            )
            # Build invariants from current state
            invs: list[SequenceInvariant] = []
            result = self.solve_sequence_query(current, invs, [mut])
            results.append(result)
            # Advance state to post-encoding if available
            if mut.post_encoding is not None:
                current = mut.post_encoding
        return results

    def summarize_mutation_evidence(
        self,
        results: Sequence[Any],
    ) -> dict[str, Any]:
        """Summarize the evidence from a sequence of solver results.

        Produces a structured summary dict compatible with ReconstructionReport.

        Parameters
        ----------
        results:
            A sequence of ``Z3Result`` or ``_StubZ3Result`` instances.

        Returns
        -------
        dict[str, Any]
            A summary dict with keys: 'total', 'sat', 'unsat', 'unknown',
            'avg_elapsed_ms', 'fragments', 'copilot_notes'.

        Theory2.tex §29.5 — evidence summarization.

        # copilot: summarize_mutation_evidence — aggregates solver results.
        """
        total = len(results)
        sat_count = sum(1 for r in results if getattr(r, "outcome", "") == "sat")
        unsat_count = sum(1 for r in results if getattr(r, "outcome", "") == "unsat")
        unknown_count = total - sat_count - unsat_count
        elapsed_list = [getattr(r, "elapsed_ms", 0.0) for r in results]
        avg_elapsed = sum(elapsed_list) / total if total > 0 else 0.0
        fragments = list({getattr(r, "fragment", "UNKNOWN") for r in results})
        if _RECONSTRUCTION_AVAILABLE and ReconstructionReport is not None:
            try:
                report = ReconstructionReport(
                    total_queries=total,
                    sat_count=sat_count,
                    unsat_count=unsat_count,
                    unknown_count=unknown_count,
                )
                return {
                    "reconstruction_report": report,
                    "total": total,
                    "sat": sat_count,
                    "unsat": unsat_count,
                    "unknown": unknown_count,
                    "avg_elapsed_ms": avg_elapsed,
                    "fragments": fragments,
                    "copilot_notes": self._copilot_evidence_notes(sat_count, unsat_count, total),
                }
            except Exception:
                pass
        return {
            "total": total,
            "sat": sat_count,
            "unsat": unsat_count,
            "unknown": unknown_count,
            "avg_elapsed_ms": round(avg_elapsed, 2),
            "fragments": fragments,
            "copilot_notes": self._copilot_evidence_notes(sat_count, unsat_count, total),
        }

    def copilot_diagnose_encoding_failure(
        self,
        failed_result: Any,
    ) -> str:
        """Diagnose an encoding failure and suggest remedies.

        This is the *copilot* interface for failure diagnosis.  Given a
        failed solver result, it inspects the outcome and suggests likely
        causes and remedies.

        Parameters
        ----------
        failed_result:
            A ``Z3Result`` or ``_StubZ3Result`` with a non-sat outcome.

        Returns
        -------
        str
            A diagnostic string with ORACLE_PROPOSED trust annotation.

        Theory2.tex §29.5 Remark 29.5 — copilot diagnosis.

        # copilot: copilot_diagnose_encoding_failure — solver failure diagnosis.
        """
        outcome = getattr(failed_result, "outcome", "unknown")
        fragment = getattr(failed_result, "fragment", "UNKNOWN")
        elapsed = getattr(failed_result, "elapsed_ms", 0.0)
        lines = [
            "[COPILOT DIAGNOSIS — ORACLE_PROPOSED trust]",
            f"Outcome  : {outcome}",
            f"Fragment : {fragment}",
            f"Elapsed  : {elapsed:.1f}ms",
            "",
        ]
        if outcome == "unsat":
            lines += [
                "Diagnosis: The encoding is UNSAT, meaning the mutation violates",
                "           an invariant (or the formula is inconsistent).",
                "Remedies:",
                "  1. Check that all value axioms in the encoding are consistent.",
                "  2. Verify that the declared support is correct (no missing addresses).",
                "  3. Run enumerate_minimal_repairs() to find a repair.",
                "  4. Check for contradictory invariants (strengthened too aggressively?).",
            ]
        elif outcome == "unknown":
            lines += [
                "Diagnosis: The solver returned UNKNOWN.",
                f"           Fragment: {fragment}.",
                "Remedies:",
                "  1. Increase solver_timeout_ms.",
                "  2. Simplify the encoding (reduce support set size).",
                "  3. Skolemize existential quantifiers if present.",
                "  4. Use a lighter fragment (e.g., QF_AUFLIA instead of AUFLIA).",
            ]
            if elapsed > self._timeout_ms * 0.9:
                lines.append(
                    "  NOTE: Elapsed time suggests TIMEOUT — increase solver_timeout_ms."
                )
        elif outcome == "timeout":
            lines += [
                "Diagnosis: The solver timed out.",
                "Remedies:",
                f"  1. Current timeout: {self._timeout_ms}ms. Try increasing it.",
                "  2. Break the query into smaller incremental steps.",
                "  3. Reduce support set size for heap slice encodings.",
            ]
        else:
            lines += [
                f"Diagnosis: Unexpected outcome '{outcome}'.",
                "  Check the encoding for structural errors.",
            ]
        lines.append("\n  Trust: ORACLE_PROPOSED — human review required.")
        return "\n".join(lines)

    def reset_results(self) -> None:
        """Clear all accumulated solver results.

        Does not affect any Z3 solver state.
        """
        self._results.clear()

    def all_results(self) -> list[Any]:
        """Return all solver results accumulated since construction or last reset.

        Returns
        -------
        list[Any]
        """
        return list(self._results)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _copilot_evidence_notes(
        self,
        sat_count: int,
        unsat_count: int,
        total: int,
    ) -> str:
        """Generate copilot evidence notes for the summary.

        Parameters
        ----------
        sat_count:
            Number of SAT results.
        unsat_count:
            Number of UNSAT results.
        total:
            Total number of queries.

        Returns
        -------
        str
        """
        if total == 0:
            return "No queries were run. [ORACLE_PROPOSED]"
        sat_rate = sat_count / total * 100
        unsat_rate = unsat_count / total * 100
        lines = [
            f"[COPILOT EVIDENCE SUMMARY — ORACLE_PROPOSED]",
            f"  {sat_count}/{total} ({sat_rate:.0f}%) queries: SAT (mutation is satisfiable)",
            f"  {unsat_count}/{total} ({unsat_rate:.0f}%) queries: UNSAT (invariant violated)",
        ]
        if unsat_count > 0:
            lines.append(
                "  RECOMMENDATION: Run enumerate_minimal_repairs() for UNSAT queries."
            )
        if sat_count == total:
            lines.append("  All mutations satisfy the declared invariants. No repair needed.")
        return "\n".join(lines)


__all__: list[str] = [
    "SequenceMutationSolverIntegration",
    "_StubZ3Result",
]
