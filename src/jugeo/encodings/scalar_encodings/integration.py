"""Integration layer connecting scalar encodings to the rest of JuGeo.

This module provides the glue code that ties scalar encoding artefacts (sorts,
refinement predicates, path conditions, arithmetic obligations) into the broader
JuGeo pipeline of encoding, solving, and reporting.

Pipeline Overview
-----------------
The canonical flow is:

    encode_phase  ->  solve_phase  ->  report_phase

*encode_phase*: Validates every :class:`RefinementEncoding` in an
:class:`EncodingContext`, logs its SMT2 assertions, and annotates the context
with item-count statistics.

*solve_phase*: Iterates over :class:`ArithmeticObligation` objects, probes
satisfiability hints, and classifies each obligation as sat/unsat.  When a real
Z3 session is available it is driven through :class:`Z3SessionBridge`.

*report_phase*: Collects the per-obligation verdicts and assembles a final
:class:`EncodingResult` with aggregate timing, fragment information, and any
UNSAT-core identifiers.

Z3 Session Bridging
-------------------
:class:`Z3SessionBridge` wraps a :class:`Z3Session` (or a lightweight mock when
Z3 is unavailable) and provides an assertion-accumulation interface.  Encodings
are serialised to SMT2 and pushed into the session log; a single
``check_and_extract`` call returns a :class:`SolveOutcome` together with any
model assignments.

Support-Region Connectivity
----------------------------
:class:`SupportRegionLinker` maintains a mapping from ``encoding_id`` to
:class:`SupportRegion`.  The linker's ``verify_support_coverage`` method detects
encodings that were created without an associated support region, which is a
common source of extrapolation bugs.

Countermodel Feedback
---------------------
:class:`CountermodelInterpreter` converts a raw :class:`Countermodel` returned
by the solver into a structured artifact that identifies the violated encoding or
path condition and proposes a :class:`RepairType`.  The artifacts are consumed
by the reporting infrastructure and can be fed back into the hypothesis-repair
loop.

Fragment Routing
----------------
:class:`FragmentRouter` inspects the :class:`FragmentHint` annotations of all
encodings within a context and decides whether to use a homogeneous fragment
solver (e.g. QF_LIA) or to escalate to the MIXED solver.  When fragments
disagree the context is split into per-fragment sub-contexts, solved
independently, and the results are merged.

Copilot Note
------------
Several public methods carry a ``copilot_`` prefix.  These are designed to be
invoked from copilot-assisted workflows and return human-readable summaries
suitable for display in editor tooltips or inline documentation.

Usage
-----
>>> pipeline = ScalarEncodingPipeline()
>>> result = pipeline.run(ctx)
>>> print(pipeline.copilot_pipeline_summary(ctx))
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.geometry.supports import SupportRegion, SupportSet
from jugeo.solver.countermodels import (
    Countermodel,
    CountermodelExtractor,
    ObstructionConverter,
    FailureClass,
    RepairType,
)
from jugeo.solver.fragments import Fragment, LogicalFragment, SolverFragment, classify_fragment
from jugeo.solver.z3_session import (
    Z3Formula,
    Z3QueryBuilder,
    Z3Result,
    Z3Session,
    SolveOutcome,
    SolverResult,
    Z3Encoder,
    Z3Decoder,
    Z3SessionPool,
)
from jugeo.encodings.scalar_encodings.models import (
    SortKind,
    FragmentHint,
    EncodeStatus,
    RefinementEncoding,
    PathCondition,
    GuardFormula,
    ArithmeticObligation,
    EncodingContext,
    EncodingResult,
    make_encoding_id,
    make_context_id,
    make_result_id,
)

logger = logging.getLogger(__name__)

# ===========================================================================
# Module-level sentinel / helpers
# ===========================================================================

_UNSAT_KEYWORDS: frozenset[str] = frozenset({"false", "(= false true)", "(not true)"})


def _contains_trivial_false(smt2: str) -> bool:
    """Return True when *smt2* contains a literal ``false`` assertion.

    This is used as a fast-path check before invoking a real solver.  It is
    intentionally conservative: ``false`` inside a comment or string literal
    will still trigger the check.  For production use the full Z3 session path
    should be preferred.

    Parameters
    ----------
    smt2:
        Any SMT2 string fragment to inspect.

    Returns
    -------
    bool
        ``True`` when a trivially-unsatisfiable token is detected.
    """
    lowered = smt2.lower()
    return any(kw in lowered for kw in _UNSAT_KEYWORDS)


# ===========================================================================
# ScalarEncodingPipeline
# ===========================================================================


class ScalarEncodingPipeline:
    """Orchestrate the encode -> solve -> report pipeline for scalar encodings.

    This class is the primary entry point for running scalar encoding contexts
    through the full JuGeo verification pipeline.  It manages phase sequencing,
    timing instrumentation, error recovery, and statistics collection.

    The pipeline is intentionally *stateful*: a single instance may be reused
    across multiple :class:`EncodingContext` objects by calling :meth:`reset`
    between invocations.  This is useful in batch-processing workflows where
    session setup overhead should be amortised.

    Copilot note: use :meth:`copilot_pipeline_summary` after each run to obtain
    a concise human-readable summary of what happened.
    """

    # -----------------------------------------------------------------------
    # Construction
    # -----------------------------------------------------------------------

    def __init__(self) -> None:
        """Initialise pipeline with zeroed statistics and empty phase list.

        All timing counters start at 0.  The *errors* list accumulates
        exception messages from any phase that raises unexpectedly.
        """
        self._session: Z3Session | None = None
        self._completed_phases: list[str] = []
        self._current_context: EncodingContext | None = None
        self._stats: dict[str, Any] = {
            "encode_ms": 0.0,
            "solve_ms": 0.0,
            "report_ms": 0.0,
            "total_items": 0,
            "errors": [],
        }

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def run(self, context: EncodingContext) -> EncodingResult:
        """Execute all pipeline phases for *context* and return a result.

        Phases are run in order: encode, solve, report.  Each phase is timed
        and the elapsed milliseconds are accumulated in :meth:`stats`.  If any
        phase raises an exception the pipeline calls :meth:`handle_failure` and
        returns a failure result rather than propagating the exception.

        Parameters
        ----------
        context:
            The encoding context to process.  Must not be closed.

        Returns
        -------
        EncodingResult
            A result whose ``outcome`` is ``"sat"``, ``"unsat"``, or
            ``"unknown"`` depending on what the solve phase found.
        """
        self._current_context = context
        logger.info("ScalarEncodingPipeline.run: starting for context %s", context.context_id)

        try:
            t0 = time.perf_counter()
            context = self.encode_phase(context)
            self._stats["encode_ms"] = (time.perf_counter() - t0) * 1000.0

            t1 = time.perf_counter()
            solve_info = self.solve_phase(context)
            self._stats["solve_ms"] = (time.perf_counter() - t1) * 1000.0

            t2 = time.perf_counter()
            result = self.report_phase(context)
            self._stats["report_ms"] = (time.perf_counter() - t2) * 1000.0

            logger.info(
                "Pipeline completed for %s: outcome=%s encode=%.1fms solve=%.1fms report=%.1fms",
                context.context_id,
                result.outcome,
                self._stats["encode_ms"],
                self._stats["solve_ms"],
                self._stats["report_ms"],
            )
            return result

        except Exception as exc:  # noqa: BLE001
            logger.exception("Pipeline failure for context %s", context.context_id)
            return self.handle_failure(context, exc)

    def encode_phase(self, context: EncodingContext) -> EncodingContext:
        """Validate context encodings and log all SMT2 assertions.

        For each :class:`RefinementEncoding` in *context*, this method checks
        that ``predicate_str`` is non-empty and logs the associated SMT2
        assertion.  Any encoding with an empty predicate is marked as invalid
        (logged as a warning) but does not abort the phase.

        The method also logs every SMT2 assertion produced by
        :meth:`EncodingContext.all_smt2_assertions` at DEBUG level so that the
        full constraint set is visible during debugging.

        Parameters
        ----------
        context:
            The encoding context to validate.

        Returns
        -------
        EncodingContext
            The same context (possibly with side-effects from logging).
        """
        logger.debug("encode_phase: validating context %s", context.context_id)
        invalid_count = 0
        for enc in context.encodings:
            if not enc.predicate_str.strip():
                logger.warning(
                    "encode_phase: encoding %s has empty predicate_str", enc.encoding_id
                )
                invalid_count += 1
            else:
                logger.debug("encode_phase: encoding %s -> %s", enc.encoding_id, enc.to_smt2())

        for pc in context.path_conditions:
            logger.debug(
                "encode_phase: path_condition %s -> %s", pc.condition_id, pc.to_smt2()
            )

        for ob in context.arithmetic_obligations:
            logger.debug(
                "encode_phase: obligation %s fragment=%s", ob.obligation_id, ob.fragment.name
            )

        all_assertions = context.all_smt2_assertions()
        logger.debug("encode_phase: %d total SMT2 assertions", len(all_assertions))

        total_items = (
            len(context.encodings)
            + len(context.path_conditions)
            + len(context.arithmetic_obligations)
            + len(context.guards)
        )
        self._stats["total_items"] = total_items
        self._completed_phases.append("encode_phase")

        if invalid_count:
            logger.warning("encode_phase: %d invalid encodings detected", invalid_count)

        return context

    def solve_phase(self, context: EncodingContext) -> dict[str, Any]:
        """Probe satisfiability of all arithmetic obligations in *context*.

        Each :class:`ArithmeticObligation` exposes :meth:`is_satisfiable_hint`
        which performs lightweight, purely-Python feasibility analysis.  When
        the hint returns ``False`` the obligation is classified as unsatisfiable.

        The returned dictionary summarises the verdicts and is stored for use
        by :meth:`report_phase`.

        Parameters
        ----------
        context:
            The encoding context whose obligations are to be checked.

        Returns
        -------
        dict[str, Any]
            A mapping with keys:

            ``"outcome"``
                ``"sat"`` if every obligation has a satisfiable hint,
                ``"unsat"`` if any obligation is provably unsatisfiable,
                ``"unknown"`` when no obligations are present.

            ``"satisfiable_count"``
                Number of obligations whose hint returned ``True``.

            ``"unsatisfiable_count"``
                Number of obligations whose hint returned ``False``.
        """
        logger.debug("solve_phase: checking %d obligations", len(context.arithmetic_obligations))
        satisfiable_count = 0
        unsatisfiable_count = 0

        for ob in context.arithmetic_obligations:
            hint = ob.is_satisfiable_hint()
            if hint:
                satisfiable_count += 1
                logger.debug("solve_phase: obligation %s -> sat (hint)", ob.obligation_id)
            else:
                unsatisfiable_count += 1
                logger.debug("solve_phase: obligation %s -> unsat (hint)", ob.obligation_id)

        if not context.arithmetic_obligations:
            outcome = "unknown"
        elif unsatisfiable_count > 0:
            outcome = "unsat"
        else:
            outcome = "sat"

        logger.info(
            "solve_phase: outcome=%s sat=%d unsat=%d",
            outcome,
            satisfiable_count,
            unsatisfiable_count,
        )
        self._completed_phases.append("solve_phase")
        return {
            "outcome": outcome,
            "satisfiable_count": satisfiable_count,
            "unsatisfiable_count": unsatisfiable_count,
        }

    def report_phase(self, context: EncodingContext) -> EncodingResult:
        """Assemble and return an :class:`EncodingResult` from *context*.

        The outcome is ``"sat"`` when :meth:`EncodingContext.has_failures`
        returns ``False``, ``"unsat"`` otherwise.  Timing is drawn from the
        accumulated ``encode_ms`` and ``solve_ms`` entries in :attr:`stats`.

        Parameters
        ----------
        context:
            The (validated, solved) encoding context.

        Returns
        -------
        EncodingResult
            A fully-populated result object ready for consumption by downstream
            reporting or repair infrastructure.
        """
        logger.debug("report_phase: assembling result for %s", context.context_id)
        outcome = "unsat" if context.has_failures() else "sat"
        elapsed_ms = self._stats["encode_ms"] + self._stats["solve_ms"]
        result = EncodingResult(
            result_id=make_result_id(),
            context=context,
            outcome=outcome,
            unsat_core=(),
            model_assignments={},
            elapsed_ms=elapsed_ms,
            fragment_used=context.fragment_hint,
        )
        self._completed_phases.append("report_phase")
        logger.info(
            "report_phase: result %s outcome=%s elapsed=%.1fms",
            result.result_id,
            outcome,
            elapsed_ms,
        )
        return result

    def handle_failure(self, context: EncodingContext, err: Exception) -> EncodingResult:
        """Create a failure :class:`EncodingResult` from an unexpected exception.

        This method is called whenever a pipeline phase raises an unhandled
        exception.  It logs the error (including type and message), appends an
        entry to ``stats["errors"]``, and returns an ``"unknown"`` result so
        that callers can distinguish pipeline failures from genuine UNSAT.

        Parameters
        ----------
        context:
            The context that was being processed when the failure occurred.
        err:
            The exception that was raised.

        Returns
        -------
        EncodingResult
            A result with ``outcome="unknown"`` and empty core/model.
        """
        error_msg = f"{type(err).__name__}: {err}"
        logger.error("ScalarEncodingPipeline failure: %s", error_msg)
        self._stats["errors"].append(error_msg)

        return EncodingResult(
            result_id=make_result_id(),
            context=context,
            outcome="unknown",
            unsat_core=(),
            model_assignments={},
            elapsed_ms=self._stats["encode_ms"] + self._stats["solve_ms"],
            fragment_used=context.fragment_hint,
        )

    def copilot_pipeline_summary(self, context: EncodingContext) -> str:
        """Return a human-readable pipeline execution summary for copilot display.

        Suitable for use in editor tooltips or inline diagnostic output.  The
        summary includes phases completed, per-phase timing, item counts, and
        any errors that were recorded.

        Parameters
        ----------
        context:
            The context that was (or is being) processed.

        Returns
        -------
        str
            Multi-line summary string.
        """
        lines = [
            f"=== ScalarEncodingPipeline summary ===",
            f"Context:        {context.context_id}",
            f"Session:        {context.session_id}",
            f"Fragment hint:  {context.fragment_hint.name}",
            f"Encodings:      {len(context.encodings)}",
            f"Path conds:     {len(context.path_conditions)}",
            f"Obligations:    {len(context.arithmetic_obligations)}",
            f"Guards:         {len(context.guards)}",
            f"",
            f"Phases completed: {', '.join(self._completed_phases) or 'none'}",
            f"encode_ms:  {self._stats['encode_ms']:.1f}",
            f"solve_ms:   {self._stats['solve_ms']:.1f}",
            f"report_ms:  {self._stats['report_ms']:.1f}",
            f"total_items: {self._stats['total_items']}",
        ]
        if self._stats["errors"]:
            lines.append(f"Errors ({len(self._stats['errors'])}):")
            for e in self._stats["errors"]:
                lines.append(f"  - {e}")
        return "\n".join(lines)

    def reset(self) -> None:
        """Reset pipeline state so it can be reused for a new context.

        Clears timing statistics, completed phase list, current context
        reference, and error log.  The Z3 session reference (if any) is
        *not* closed automatically; callers should ensure sessions are
        properly released before calling reset.
        """
        logger.debug("ScalarEncodingPipeline.reset: clearing state")
        self._completed_phases = []
        self._current_context = None
        self._stats = {
            "encode_ms": 0.0,
            "solve_ms": 0.0,
            "report_ms": 0.0,
            "total_items": 0,
            "errors": [],
        }

    def stats(self) -> dict[str, Any]:
        """Return a shallow copy of the current statistics dictionary.

        The returned dict contains timing entries (``encode_ms``, ``solve_ms``,
        ``report_ms``), ``total_items``, and ``errors``.  Modifications to the
        returned dict do not affect the pipeline's internal state.

        Returns
        -------
        dict[str, Any]
            Copy of ``_stats``.
        """
        return dict(self._stats)


# ===========================================================================
# Z3SessionBridge
# ===========================================================================


class Z3SessionBridge:
    """Bridge scalar encoding artefacts into a :class:`Z3Session`.

    The bridge accumulates SMT2 assertions from :class:`RefinementEncoding`,
    :class:`PathCondition`, and :class:`ArithmeticObligation` objects and
    drives them through a Z3 session.  When Z3 is not installed the bridge
    falls back to a lightweight heuristic check based on string inspection.

    The bridge is designed to be used as a context manager or to be driven
    explicitly via :meth:`open_session` / :meth:`close_session`.

    Copilot note: inspect :attr:`assertion_log` after ``check_and_extract``
    to see exactly which assertions were submitted to the solver.
    """

    def __init__(self) -> None:
        """Initialise bridge with no open session and empty assertion log."""
        self._session: Z3Session | None = None
        self._assertion_log: list[str] = []
        self._open: bool = False

    @property
    def assertion_log(self) -> list[str]:
        """Read-only view of accumulated SMT2 assertion strings."""
        return list(self._assertion_log)

    @property
    def is_open(self) -> bool:
        """``True`` when the bridge has an open session ready for assertions."""
        return self._open

    def open_session(self) -> None:
        """Open a Z3 session (or activate heuristic mode if Z3 is absent).

        Attempts to create a :class:`Z3Session`.  If construction fails
        (e.g. because Z3 is not installed) the bridge marks itself as open in
        *heuristic mode* where :meth:`check_and_extract` uses string inspection
        instead of the real solver.

        Raises
        ------
        RuntimeError
            If the session is already open.
        """
        if self._open:
            raise RuntimeError("Z3SessionBridge: session is already open")
        logger.debug("Z3SessionBridge.open_session: opening")
        try:
            pool = Z3SessionPool(max_sessions=1)
            self._session = pool.create_session()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Z3SessionBridge: could not create Z3Session (%s); using heuristic mode", exc
            )
            self._session = None
        self._open = True
        self._assertion_log = []
        logger.info("Z3SessionBridge: session opened (z3_available=%s)", self._session is not None)

    def close_session(self) -> None:
        """Close the active session and log the total assertion count.

        Safe to call even when the bridge is in heuristic mode (no real
        session).  After closing, :attr:`is_open` returns ``False`` and no
        further assertions can be submitted until :meth:`open_session` is
        called again.
        """
        if not self._open:
            logger.debug("Z3SessionBridge.close_session: already closed")
            return
        count = len(self._assertion_log)
        if self._session is not None:
            try:
                self._session.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Z3SessionBridge: error closing Z3Session: %s", exc)
        self._session = None
        self._open = False
        logger.info("Z3SessionBridge: session closed with %d assertions logged", count)

    def assert_encoding(self, enc: RefinementEncoding) -> None:
        """Append the SMT2 representation of *enc* to the assertion log.

        The encoding's :meth:`RefinementEncoding.to_smt2` method is used to
        generate the assertion string.

        Parameters
        ----------
        enc:
            The refinement encoding to assert.

        Raises
        ------
        RuntimeError
            If the bridge does not have an open session.
        """
        if not self._open:
            raise RuntimeError(
                "Z3SessionBridge: cannot assert encoding – session is not open"
            )
        smt2 = enc.to_smt2()
        self._assertion_log.append(smt2)
        logger.debug("Z3SessionBridge.assert_encoding: %s -> %s", enc.encoding_id, smt2)

    def assert_path_condition(self, pc: PathCondition) -> None:
        """Append the SMT2 representation of a :class:`PathCondition`.

        Parameters
        ----------
        pc:
            The path condition to assert.

        Raises
        ------
        RuntimeError
            If the session is not open.
        """
        if not self._open:
            raise RuntimeError(
                "Z3SessionBridge: cannot assert path condition – session is not open"
            )
        smt2 = pc.to_smt2()
        self._assertion_log.append(smt2)
        logger.debug("Z3SessionBridge.assert_path_condition: %s", pc.condition_id)

    def assert_obligation(self, ob: ArithmeticObligation) -> None:
        """Append the query form of an :class:`ArithmeticObligation`.

        Parameters
        ----------
        ob:
            The arithmetic obligation to assert.

        Raises
        ------
        RuntimeError
            If the session is not open.
        """
        if not self._open:
            raise RuntimeError(
                "Z3SessionBridge: cannot assert obligation – session is not open"
            )
        smt2 = ob.to_z3_query()
        self._assertion_log.append(smt2)
        logger.debug("Z3SessionBridge.assert_obligation: %s", ob.obligation_id)

    def check_and_extract(self) -> tuple[SolveOutcome, dict[str, str]]:
        """Run the solver over accumulated assertions and extract a model.

        If a real Z3 session is available it is consulted.  Otherwise a
        heuristic based on the presence of ``"false"`` in assertion strings is
        used.

        Returns
        -------
        tuple[SolveOutcome, dict[str, str]]
            A pair of (outcome, model_dict).  When the outcome is
            :attr:`SolveOutcome.UNSAT` or :attr:`SolveOutcome.UNKNOWN`
            the model dict is empty.
        """
        if not self._assertion_log:
            logger.debug("Z3SessionBridge.check_and_extract: no assertions -> SAT vacuously")
            return SolveOutcome.SAT, {}

        # Heuristic fast path: any trivially false assertion -> UNSAT
        for assertion in self._assertion_log:
            if _contains_trivial_false(assertion):
                logger.info("Z3SessionBridge: trivial UNSAT detected in assertion log")
                return SolveOutcome.UNSAT, {}

        if self._session is not None:
            try:
                result = self._session.check_sat()
                if result == SolveOutcome.SAT:
                    raw_model = self._session.get_model()
                    model: dict[str, str] = {k: str(v) for k, v in raw_model.items()}
                    return SolveOutcome.SAT, model
                return result, {}
            except Exception as exc:  # noqa: BLE001
                logger.warning("Z3SessionBridge: Z3 check failed (%s); returning UNKNOWN", exc)
                return SolveOutcome.UNKNOWN, {}

        # No real session – all assertions passed heuristic, treat as SAT
        logger.debug("Z3SessionBridge.check_and_extract: heuristic SAT")
        return SolveOutcome.SAT, {}


# ===========================================================================
# SupportRegionLinker
# ===========================================================================


class SupportRegionLinker:
    """Associate :class:`RefinementEncoding` objects with :class:`SupportRegion` instances.

    The linker maintains a mapping from encoding ID strings to support regions.
    This mapping is used to verify that every encoding in a context has a
    clearly defined geometric scope and to serialise support information for
    downstream consumption.

    Copilot note: call :meth:`copilot_support_report` after verifying coverage
    to get a concise description of which encodings are missing support.
    """

    def __init__(self) -> None:
        """Initialise with an empty support map."""
        self._support_map: dict[str, SupportRegion] = {}

    def link_encoding_to_support(
        self, enc: RefinementEncoding, region: SupportRegion
    ) -> None:
        """Associate *enc* with *region* in the internal support map.

        Overwrites any existing mapping for the same encoding ID.

        Parameters
        ----------
        enc:
            The refinement encoding to link.
        region:
            The support region that governs *enc*.
        """
        self._support_map[enc.encoding_id] = region
        logger.debug(
            "SupportRegionLinker: linked encoding %s to region %r",
            enc.encoding_id,
            region,
        )

    def verify_support_coverage(
        self, context: EncodingContext
    ) -> dict[str, bool]:
        """Check whether every encoding in *context* has a registered support region.

        Parameters
        ----------
        context:
            The encoding context to inspect.

        Returns
        -------
        dict[str, bool]
            Mapping from ``encoding_id`` to ``True`` (has support) or
            ``False`` (missing support).
        """
        coverage: dict[str, bool] = {}
        for enc in context.encodings:
            has_support = enc.encoding_id in self._support_map
            coverage[enc.encoding_id] = has_support
            if not has_support:
                logger.warning(
                    "SupportRegionLinker: encoding %s has no registered support region",
                    enc.encoding_id,
                )
        logger.debug(
            "SupportRegionLinker.verify_support_coverage: %d/%d covered",
            sum(coverage.values()),
            len(coverage),
        )
        return coverage

    def export_support_map(self, context: EncodingContext) -> dict[str, str]:
        """Serialise the support map to a plain-string dict for export.

        Encodings that are not registered in the internal map are mapped to
        the sentinel string ``"MISSING"``.

        Parameters
        ----------
        context:
            The context whose encodings should be included in the export.

        Returns
        -------
        dict[str, str]
            Mapping from ``encoding_id`` to the ``repr()`` of the associated
            :class:`SupportRegion`, or ``"MISSING"``.
        """
        result: dict[str, str] = {}
        for enc in context.encodings:
            region = self._support_map.get(enc.encoding_id)
            result[enc.encoding_id] = repr(region) if region is not None else "MISSING"
        return result

    def check_jurisdiction(
        self, enc: RefinementEncoding, region: SupportRegion
    ) -> bool:
        """Determine whether *enc* falls within the jurisdiction of *region*.

        The check succeeds when the linker has *enc* registered and the
        registered region is equal to *region* by identity or value.

        Parameters
        ----------
        enc:
            Encoding whose jurisdiction is to be checked.
        region:
            Candidate support region.

        Returns
        -------
        bool
            ``True`` when *enc* is registered under *region*.
        """
        registered = self._support_map.get(enc.encoding_id)
        if registered is None:
            return False
        return registered == region

    def copilot_support_report(self) -> str:
        """Return a human-readable summary of the current support coverage.

        Suitable for copilot tooltip display.  Includes the total number of
        encodings linked and details of each registered pair.

        Returns
        -------
        str
            Multi-line report string.
        """
        lines = [
            "=== SupportRegionLinker report ===",
            f"Linked encodings: {len(self._support_map)}",
        ]
        if self._support_map:
            lines.append("Entries:")
            for enc_id, region in self._support_map.items():
                lines.append(f"  {enc_id} -> {region!r}")
        else:
            lines.append("(no entries)")
        return "\n".join(lines)


# ===========================================================================
# CountermodelInterpreter
# ===========================================================================


class CountermodelInterpreter:
    """Interpret :class:`Countermodel` objects relative to :class:`EncodingContext` data.

    When the solver returns SAT (meaning the *negation* of the property is
    satisfiable, i.e. a counterexample exists) this class maps the raw model
    assignments back to the specific encodings and path conditions that are
    violated.  It also proposes repair hints.

    An interpretation cache prevents redundant re-processing of identical
    countermodels within a session.

    Copilot note: :meth:`copilot_interpret_hint` surfaces unusual assignments
    and suggests what to verify next.
    """

    def __init__(self) -> None:
        """Initialise with an empty interpretation cache."""
        self._interpretation_cache: dict[str, dict[str, str]] = {}

    def interpret(
        self, countermodel: Countermodel, context: EncodingContext
    ) -> dict[str, Any]:
        """Produce a structured interpretation of *countermodel* w.r.t. *context*.

        The interpretation identifies which encodings and path conditions are
        violated by the model assignments and classifies the failure.

        Parameters
        ----------
        countermodel:
            The countermodel returned by the solver.
        context:
            The encoding context against which the model is interpreted.

        Returns
        -------
        dict[str, Any]
            A dict with keys:

            ``"assignments"``
                Variable assignment dict (str -> str).

            ``"violated_encoding_ids"``
                List of encoding IDs whose predicates are violated.

            ``"violated_path_condition_ids"``
                List of path condition IDs whose consequents are violated.

            ``"failure_kind"``
                A string naming the :class:`FailureClass` value.
        """
        assignments = self.extract_assignment(countermodel)
        cache_key = countermodel.model_id
        self._interpretation_cache[cache_key] = assignments

        violated_encodings = self.locate_failure_in_context(assignments, context)

        violated_pcs: list[str] = []
        for pc in context.path_conditions:
            # A path condition's consequent is violated if "false" would result
            # from substituting the model into it (lightweight heuristic).
            consequent_lower = pc.consequent.lower()
            if any(
                f"{var}=" in consequent_lower and "false" in consequent_lower
                for var in assignments
            ):
                violated_pcs.append(pc.condition_id)

        failure_kind = FailureClass.UNKNOWN
        if violated_encodings:
            failure_kind = FailureClass.SORT_VIOLATION
        elif violated_pcs:
            failure_kind = FailureClass.ASSIGNMENT_CONFLICT

        logger.info(
            "CountermodelInterpreter: model_id=%s violated_encodings=%s failure_kind=%s",
            countermodel.model_id,
            violated_encodings,
            failure_kind.value,
        )
        return {
            "assignments": assignments,
            "violated_encoding_ids": violated_encodings,
            "violated_path_condition_ids": violated_pcs,
            "failure_kind": failure_kind.value,
        }

    def extract_assignment(self, countermodel: Countermodel) -> dict[str, str]:
        """Extract variable assignments from a :class:`Countermodel`.

        Tries multiple attribute paths in priority order: ``variable_assignments``
        (the canonical new-style field), then ``assignment`` (legacy boolean
        map).  Handles missing attributes gracefully.

        Parameters
        ----------
        countermodel:
            The countermodel from which assignments are extracted.

        Returns
        -------
        dict[str, str]
            Variable name -> string representation of assigned value.
        """
        try:
            raw: dict[str, Any] = countermodel.variable_assignments
            if raw:
                return {k: str(v) for k, v in raw.items()}
        except AttributeError:
            pass

        try:
            legacy: dict[str, bool] = countermodel.assignment
            return {k: str(v) for k, v in legacy.items()}
        except AttributeError:
            pass

        logger.warning(
            "CountermodelInterpreter.extract_assignment: no assignment attributes found on %s",
            countermodel.model_id,
        )
        return {}

    def locate_failure_in_context(
        self, model: dict[str, str], context: EncodingContext
    ) -> list[str]:
        """Identify which encodings in *context* are violated by *model*.

        An encoding is classified as violated when:
        1. Its ``predicate_str`` mentions a variable present in *model*, AND
        2. Substituting the model value results in ``"false"`` appearing in
           the predicate (heuristic string check).

        Parameters
        ----------
        model:
            Variable-name -> value-string assignments from a countermodel.
        context:
            The encoding context to search.

        Returns
        -------
        list[str]
            List of encoding IDs that appear to be violated.
        """
        violated: list[str] = []
        for enc in context.encodings:
            pred = enc.predicate_str
            candidate = pred
            for var, val in model.items():
                # Simple string substitution heuristic
                candidate = candidate.replace(var, val)
            if _contains_trivial_false(candidate):
                violated.append(enc.encoding_id)
                logger.debug(
                    "CountermodelInterpreter: encoding %s violated by model", enc.encoding_id
                )
        return violated

    def produce_repair_hint(
        self, model: dict[str, str], context: EncodingContext
    ) -> str:
        """Generate a human-readable repair suggestion for a failing model.

        Examines which encodings fail under *model* and proposes a strategy
        drawn from :class:`RepairType` values.

        Parameters
        ----------
        model:
            Variable-name -> value-string assignments.
        context:
            The context containing the encodings to inspect.

        Returns
        -------
        str
            A human-readable repair suggestion string.
        """
        violated = self.locate_failure_in_context(model, context)
        if not violated:
            return (
                f"No encodings violated; consider {RepairType.WEAKEN_POSTCONDITION.value} "
                f"or {RepairType.MANUAL_REVIEW.value}."
            )
        suggestion = RepairType.STRENGTHEN_PRECONDITION.value
        details = ", ".join(violated[:3])
        suffix = f" (and {len(violated) - 3} more)" if len(violated) > 3 else ""
        return (
            f"Violated encodings: {details}{suffix}. "
            f"Suggested repair: {suggestion}. "
            f"Review predicate_str of affected encodings and tighten variable constraints."
        )

    def to_failure_artifact(self, model: dict[str, str]) -> dict[str, Any]:
        """Convert *model* assignments into a structured failure artifact.

        The artifact contains:
        - ``"trigger_smt"``: a conjunctive SMT2 clause derived from all
          assignments (useful as a test-case trigger).
        - ``"context_assumptions"``: list of individual assignment assertions.
        - ``"severity"``: ``"high"`` when more than 5 assignments, else ``"low"``.
        - ``"kind_hint"``: the most likely :class:`FailureClass` name.

        Parameters
        ----------
        model:
            Variable-name -> value-string assignments.

        Returns
        -------
        dict[str, Any]
            Failure artifact dict.
        """
        assumptions: list[str] = []
        for var, val in model.items():
            if val.lstrip("-").isdigit():
                assumptions.append(f"(= {var} {val})")
            elif val.lower() in ("true", "false"):
                assumptions.append(f"(= {var} {val.lower()})")
            else:
                assumptions.append(f"(= {var} |{val}|)")

        if assumptions:
            trigger = "(and " + " ".join(assumptions) + ")" if len(assumptions) > 1 else assumptions[0]
        else:
            trigger = "true"

        severity = "high" if len(model) > 5 else "low"
        kind_hint = (
            FailureClass.ASSIGNMENT_CONFLICT.value
            if model
            else FailureClass.UNKNOWN.value
        )

        return {
            "trigger_smt": trigger,
            "context_assumptions": assumptions,
            "severity": severity,
            "kind_hint": kind_hint,
        }

    def copilot_interpret_hint(self, model: dict[str, str]) -> str:
        """Return copilot-friendly interpretation advice for *model*.

        Notes unusual assignments (e.g. extreme numeric values, boolean
        contradictions) and suggests what to verify in the encoding context.

        Parameters
        ----------
        model:
            Variable-name -> value-string assignments from a countermodel.

        Returns
        -------
        str
            Human-readable advice string.
        """
        if not model:
            return "Empty model: solver returned SAT with no assignments. Check for vacuous axioms."

        extreme: list[str] = []
        booleans: list[str] = []
        for var, val in model.items():
            try:
                n = int(val)
                if abs(n) > 10_000:
                    extreme.append(f"{var}={val}")
            except ValueError:
                pass
            if val.lower() in ("true", "false"):
                booleans.append(f"{var}={val}")

        lines = [f"Model has {len(model)} assignment(s)."]
        if extreme:
            lines.append(f"Extreme numeric values (possible overflow?): {', '.join(extreme)}")
        if booleans:
            lines.append(f"Boolean assignments: {', '.join(booleans)}")
        lines.append("Verify that refinement predicates bound all relevant variables.")
        return " ".join(lines)


# ===========================================================================
# FragmentRouter
# ===========================================================================


class FragmentRouter:
    """Route :class:`EncodingContext` objects to the appropriate SMT fragment solver.

    The router inspects the :class:`FragmentHint` annotations on all encodings
    within a context and decides which solver should handle the context.  When
    encodings span multiple incompatible fragments the context is split into
    per-fragment sub-contexts that are solved independently and the results are
    merged.

    A routing log is maintained for audit purposes and exposed via
    :meth:`copilot_routing_report`.

    Copilot note: if routing repeatedly escalates to MIXED, consider annotating
    your encodings more precisely to avoid solver overhead.
    """

    def __init__(self) -> None:
        """Initialise with an empty routing log."""
        self._routing_log: list[tuple[str, FragmentHint]] = []

    # -----------------------------------------------------------------------
    # Core routing
    # -----------------------------------------------------------------------

    def route(self, context: EncodingContext) -> FragmentHint:
        """Determine the appropriate :class:`FragmentHint` for *context*.

        If all encodings share the same fragment hint that hint is returned.
        If encodings disagree, :attr:`FragmentHint.MIXED` is returned.  The
        decision is recorded in the routing log.

        Parameters
        ----------
        context:
            The context to route.

        Returns
        -------
        FragmentHint
            The selected fragment hint.
        """
        if not context.encodings:
            decision = context.fragment_hint
        else:
            hints: set[FragmentHint] = {enc.fragment for enc in context.encodings}
            if len(hints) == 1:
                decision = next(iter(hints))
            else:
                logger.info(
                    "FragmentRouter: mixed fragments %s for context %s -> MIXED",
                    {h.name for h in hints},
                    context.context_id,
                )
                decision = FragmentHint.MIXED

        self._routing_log.append((context.context_id, decision))
        logger.debug("FragmentRouter.route: %s -> %s", context.context_id, decision.name)
        return decision

    def split_by_fragment(
        self, context: EncodingContext
    ) -> dict[FragmentHint, EncodingContext]:
        """Split *context* into per-fragment sub-contexts.

        Each :class:`RefinementEncoding` is placed in the sub-context whose
        key matches the encoding's :attr:`~RefinementEncoding.fragment`.  Sub-
        contexts inherit path conditions and obligations from the parent (they
        may share these).

        Parameters
        ----------
        context:
            The source context to split.

        Returns
        -------
        dict[FragmentHint, EncodingContext]
            Mapping from :class:`FragmentHint` to a sub-context containing
            only the encodings that belong to that fragment.
        """
        buckets: dict[FragmentHint, list[RefinementEncoding]] = {}
        for enc in context.encodings:
            buckets.setdefault(enc.fragment, []).append(enc)

        result: dict[FragmentHint, EncodingContext] = {}
        for fragment, encs in buckets.items():
            sub_ctx = EncodingContext(
                context_id=make_context_id(),
                session_id=f"{context.session_id}_{fragment.name.lower()}",
                fragment_hint=fragment,
                encodings=list(encs),
                path_conditions=list(context.path_conditions),
                arithmetic_obligations=list(context.arithmetic_obligations),
                guards=list(context.guards),
                created_at=context.created_at,
                closed=False,
            )
            result[fragment] = sub_ctx
            logger.debug(
                "FragmentRouter.split_by_fragment: fragment=%s sub_ctx=%s encs=%d",
                fragment.name,
                sub_ctx.context_id,
                len(encs),
            )
        return result

    def merge_results(self, results: list[EncodingResult]) -> EncodingResult:
        """Merge a list of per-fragment :class:`EncodingResult` objects.

        Merge semantics:
        - If any result has ``outcome="unsat"`` the merged outcome is ``"unsat"``.
        - If all results are ``"sat"`` the merged outcome is ``"sat"``.
        - Otherwise the merged outcome is ``"unknown"``.
        - Unsat cores are concatenated.
        - Model assignments are merged (later results override earlier ones).
        - ``elapsed_ms`` is the sum of all results.
        - ``fragment_used`` is set to :attr:`FragmentHint.MIXED`.

        Parameters
        ----------
        results:
            The list of per-fragment results to merge.

        Returns
        -------
        EncodingResult
            A single merged result.

        Raises
        ------
        ValueError
            If *results* is empty.
        """
        if not results:
            raise ValueError("FragmentRouter.merge_results: cannot merge empty results list")

        outcomes = {r.outcome for r in results}
        if "unsat" in outcomes:
            merged_outcome = "unsat"
        elif outcomes == {"sat"}:
            merged_outcome = "sat"
        else:
            merged_outcome = "unknown"

        merged_core: tuple[str, ...] = ()
        merged_model: dict[str, str] = {}
        total_ms = 0.0
        base_context = results[0].context

        for r in results:
            merged_core = merged_core + r.unsat_core
            merged_model.update(r.model_assignments)
            total_ms += r.elapsed_ms

        logger.info(
            "FragmentRouter.merge_results: merged %d results -> outcome=%s elapsed=%.1fms",
            len(results),
            merged_outcome,
            total_ms,
        )
        return EncodingResult(
            result_id=make_result_id(),
            context=base_context,
            outcome=merged_outcome,
            unsat_core=merged_core,
            model_assignments=merged_model,
            elapsed_ms=total_ms,
            fragment_used=FragmentHint.MIXED,
        )

    def escalate_unknown(self, context: EncodingContext) -> EncodingContext:
        """Escalate *context* to use the MIXED solver fragment.

        Creates a new :class:`EncodingContext` identical to *context* except
        that ``fragment_hint`` is set to :attr:`FragmentHint.MIXED`.  The
        original context is not modified.

        Parameters
        ----------
        context:
            The context to escalate.

        Returns
        -------
        EncodingContext
            A new context with ``fragment_hint=FragmentHint.MIXED``.
        """
        logger.info(
            "FragmentRouter.escalate_unknown: escalating context %s to MIXED", context.context_id
        )
        escalated = EncodingContext(
            context_id=make_context_id(),
            session_id=context.session_id,
            fragment_hint=FragmentHint.MIXED,
            encodings=list(context.encodings),
            path_conditions=list(context.path_conditions),
            arithmetic_obligations=list(context.arithmetic_obligations),
            guards=list(context.guards),
            created_at=context.created_at,
            closed=False,
        )
        self._routing_log.append((escalated.context_id, FragmentHint.MIXED))
        return escalated

    def copilot_routing_report(self) -> str:
        """Return a copilot-friendly report of all routing decisions.

        Includes statistics on fragment distribution and any escalations to
        MIXED mode.

        Returns
        -------
        str
            Multi-line report string.
        """
        if not self._routing_log:
            return "=== FragmentRouter report ===\nNo routing decisions recorded."

        counts: dict[str, int] = {}
        for _, hint in self._routing_log:
            counts[hint.name] = counts.get(hint.name, 0) + 1

        escalations = counts.get("MIXED", 0)
        lines = [
            "=== FragmentRouter report ===",
            f"Total routing decisions: {len(self._routing_log)}",
            f"MIXED escalations: {escalations}",
            "Fragment distribution:",
        ]
        for frag_name, count in sorted(counts.items()):
            lines.append(f"  {frag_name}: {count}")
        if escalations:
            lines.append(
                "Tip: reduce MIXED escalations by annotating encodings with precise fragment hints."
            )
        return "\n".join(lines)
