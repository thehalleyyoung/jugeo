r"""theory2.tex Ch31 §31.4 — Full Model Reconstruction Pipeline.

After Z3 returns a satisfying model, the reconstruction pipeline extracts
assignments, assembles partial models, validates consistency, annotates
with trust levels, and packages the result as JuGeo evidence.

Pipeline phases (§31.4.1):

.. math::

   \\text{Pipeline} =
   \\text{Extraction}
   \\to \\text{Assembly}
   \\to \\text{Validation}
   \\to \\text{TrustAnnotation}
   \\to \\text{Packaging}
   \\to \\text{Complete}

Partial model assembly (§31.4.2):

.. math::

   M_1 \\sqcup M_2
   = \\lambda x.\\,
   \\begin{cases}
     M_1(x) & \\text{if } x \\in \\mathrm{dom}(M_1) \\\\
     M_2(x) & \\text{if } x \\in \\mathrm{dom}(M_2) \\setminus \\mathrm{dom}(M_1) \\\\
     \\bot   & \\text{otherwise}
   \\end{cases}

The join :math:`M_1 \\sqcup M_2` is the *left-biased union* of two partial
models: ``M_1`` takes precedence on any variable in its domain, and
variables from ``M_2`` are added only when they are absent from ``M_1``.
Variables absent from both models remain undefined (bottom, :math:`\\bot`).

§31.4.3 — Trust annotation
----------------------------
Every reconstructed assignment is annotated with a trust level from the
JuGeo trust algebra (§17):

.. math::

   \\text{TrustLevel} \\in
   \\{\\text{UNVERIFIED}, \\text{AUTOMATED}, \\text{COPILOT\\_PROPOSED},
     \\text{SOLVER\\_INFERRED}, \\text{HUMAN\\_REVIEWED}\\}

The partial order on trust levels is:

.. math::

   \\text{UNVERIFIED}
   \\prec \\text{AUTOMATED}
   \\prec \\text{COPILOT\\_PROPOSED}
   \\prec \\text{SOLVER\\_INFERRED}
   \\prec \\text{HUMAN\\_REVIEWED}

§31.4.4 — Evidence packaging
------------------------------
The packager signs each evidence fragment with a SHA-256 digest so that
downstream consumers can detect tampering or accidental mutation.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
import dataclasses
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Optional jugeo subpackage imports — gracefully degrade when unavailable
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder, Z3Decoder, Z3Result
    _Z3_SESSION_AVAILABLE = True
except ImportError:
    _Z3_SESSION_AVAILABLE = False
    class Z3Session: pass  # type: ignore[misc]
    class Z3Formula: pass  # type: ignore[misc]
    class Z3Encoder: pass  # type: ignore[misc]
    class Z3Decoder: pass  # type: ignore[misc]
    class Z3Result: pass  # type: ignore[misc]

try:
    from jugeo.solver.reconstruction import ModelReconstructor as SolverModelReconstruction
    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    _RECONSTRUCTION_AVAILABLE = False
    class SolverModelReconstruction: pass  # type: ignore[misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, Judgment
    _JUDGMENTS_AVAILABLE = True
except ImportError:
    _JUDGMENTS_AVAILABLE = False
    class JudgmentTerm: pass  # type: ignore[misc]
    class Judgment: pass  # type: ignore[misc]

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False
    class TrustAlgebra: pass  # type: ignore[misc]
    class TrustLevel: pass  # type: ignore[misc]


# ---------------------------------------------------------------------------
# §31.4 Enumerations
# ---------------------------------------------------------------------------

# The ordered trust levels used throughout the annotation and downgrade logic.
_TRUST_ORDER: list[str] = [
    "UNVERIFIED",
    "AUTOMATED",
    "COPILOT_PROPOSED",
    "SOLVER_INFERRED",
    "HUMAN_REVIEWED",
]


class AssemblyPhase(str, Enum):
    """The six sequential phases of the model reconstruction pipeline.

    Each phase transforms the data produced by the previous phase into a
    richer, more structured representation.  The pipeline progresses
    monotonically: once a phase is complete it is never re-entered except
    by constructing a new :class:`ReconstructionPipeline` instance.

    The phases correspond directly to §31.4.1 of theory2.tex:

    1. **EXTRACTION** — parse the raw Z3 model dictionary.
    2. **ASSEMBLY**   — group extracted variables by prefix / sort.
    3. **VALIDATION** — check the assembled model for consistency.
    4. **TRUST_ANNOTATION** — attach a trust level to each assignment.
    5. **PACKAGING**  — wrap in a signed evidence envelope.
    6. **COMPLETE**   — terminal state; no further transitions.
    """

    EXTRACTION = "extraction"
    """Raw variable assignments are extracted from the Z3 model dict."""

    ASSEMBLY = "assembly"
    """Extracted variables are grouped and structured into a partial model."""

    VALIDATION = "validation"
    """The assembled model is checked for type and domain consistency."""

    TRUST_ANNOTATION = "trust_annotation"
    """Each model assignment is tagged with a trust-level annotation."""

    PACKAGING = "packaging"
    """The annotated model is wrapped in a signed evidence envelope."""

    COMPLETE = "complete"
    """Pipeline has finished successfully; result is in ``results``."""


class CompletionStrategy(str, Enum):
    """Strategy used when completing a partial model with undefined variables.

    When a Z3 model does not assign values to all variables in scope,
    the assembler must choose how to fill the gaps.  The choice of
    strategy trades completeness against soundness:

    - **CONSERVATIVE** assigns the most restrictive default (zero / false /
      empty string), minimizing the chance of spurious proofs.
    - **LIBERAL** picks any value from the variable's sort universe,
      potentially enabling more conclusions.
    - **COPILOT_ASSISTED** delegates to a hints dictionary supplied by the
      Copilot subsystem.
    - **MINIMAL** / **MAXIMAL** use the smallest / largest value from the
      sort universe respectively.
    """

    CONSERVATIVE = "conservative"
    """Use minimal / zero-like defaults for all undefined variables."""

    LIBERAL = "liberal"
    """Use the first available universe element for undefined variables."""

    COPILOT_ASSISTED = "copilot_assisted"
    """Use hints supplied by the Copilot subsystem for completion."""

    MINIMAL = "minimal"
    """Use the smallest value (by natural order) from each sort universe."""

    MAXIMAL = "maximal"
    """Use the largest value (by natural order) from each sort universe."""


# ---------------------------------------------------------------------------
# §31.4.1 — ReconstructionPipeline
# ---------------------------------------------------------------------------


@dataclass
class ReconstructionPipeline:
    """Orchestrator for the five-phase model reconstruction pipeline.

    A :class:`ReconstructionPipeline` is a stateful object that drives a
    Z3 model through the extraction → assembly → validation →
    trust-annotation → packaging sequence.  Progress is tracked via
    :attr:`current_phase`, and both intermediate results and errors are
    accumulated in :attr:`results` and :attr:`errors` respectively.

    Parameters
    ----------
    pipeline_id:
        UUID string uniquely identifying this pipeline run.
    phases:
        Ordered list of :class:`AssemblyPhase` values to traverse.
        Populated automatically from the enum definition when empty.
    current_phase:
        The phase the pipeline is currently executing.
    results:
        Accumulator dictionary for phase outputs, keyed by phase name.
    errors:
        List of human-readable error messages encountered during the run.
    metadata:
        Free-form metadata dictionary (timestamps, counts, etc.).

    Notes
    -----
    All ``run_*`` methods are designed to be called in sequence, with
    the output of each passed to the input of the next.  The
    :meth:`full_run` convenience method chains them automatically.
    """

    pipeline_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    phases: list[AssemblyPhase] = field(default_factory=list)
    current_phase: AssemblyPhase = AssemblyPhase.EXTRACTION
    results: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Populate ``phases`` with the complete ordered phase list if empty."""
        if not self.phases:
            self.phases = list(AssemblyPhase)

    # ------------------------------------------------------------------
    # Phase management
    # ------------------------------------------------------------------

    def advance_phase(self) -> AssemblyPhase:
        """Advance the pipeline to the next phase and return it.

        If the pipeline is already at the last phase (:attr:`AssemblyPhase.COMPLETE`),
        this method is idempotent and returns the current phase unchanged.

        Returns
        -------
        AssemblyPhase
            The new current phase after advancing.
        """
        try:
            current_idx = self.phases.index(self.current_phase)
        except ValueError:
            # current_phase not found in phases list — stay put.
            return self.current_phase

        next_idx = current_idx + 1
        if next_idx >= len(self.phases):
            # Already at the last phase; do not advance past the end.
            return self.current_phase

        self.current_phase = self.phases[next_idx]
        self.metadata[f"entered_{self.current_phase.value}_at"] = time.time()
        return self.current_phase

    def record_result(self, key: str, value: Any) -> None:
        """Store a phase result under *key* in :attr:`results`.

        Also records a timestamp in :attr:`metadata` so that audit logs
        can reconstruct the timeline of the pipeline run.

        Parameters
        ----------
        key:
            The result key, typically the phase name or a descriptive label.
        value:
            The result value; must be JSON-serializable for later packaging.
        """
        self.results[key] = value
        self.metadata[f"recorded_{key}_at"] = time.time()

    def record_error(self, error: str) -> None:
        """Append a human-readable error message to :attr:`errors`.

        Errors do not halt the pipeline automatically; callers may choose
        to inspect :meth:`has_errors` and abort or continue based on
        their recovery policy.

        Parameters
        ----------
        error:
            A descriptive error message string.
        """
        self.errors.append(error)
        self.metadata["last_error_at"] = time.time()
        self.metadata["error_count"] = len(self.errors)

    def is_complete(self) -> bool:
        """Return ``True`` iff the pipeline has reached :attr:`AssemblyPhase.COMPLETE`.

        Returns
        -------
        bool
            ``True`` when ``current_phase == AssemblyPhase.COMPLETE``.
        """
        return self.current_phase == AssemblyPhase.COMPLETE

    def has_errors(self) -> bool:
        """Return ``True`` iff at least one error has been recorded.

        Returns
        -------
        bool
            ``True`` when ``len(self.errors) > 0``.
        """
        return len(self.errors) > 0

    # ------------------------------------------------------------------
    # Pipeline phases
    # ------------------------------------------------------------------

    def run_extraction(self, model_dict: dict[str, Any]) -> dict[str, Any]:
        """Phase 1 — Extract and classify raw Z3 model assignments.

        Iterates over *model_dict* and partitions keys by the Python type
        of their values: ``bool``, ``int``, ``str``, or ``other``.  This
        classification is used by the assembly phase to apply sort-specific
        grouping heuristics.

        Parameters
        ----------
        model_dict:
            The raw dictionary mapping variable names to their Z3 model
            values (already converted to Python objects by the decoder).

        Returns
        -------
        dict[str, Any]
            A structured extraction result with the following keys:

            - ``"assignments"`` — the original *model_dict* verbatim.
            - ``"bool_vars"`` — variables whose values are ``bool``.
            - ``"int_vars"``  — variables whose values are ``int``.
            - ``"str_vars"``  — variables whose values are ``str``.
            - ``"other_vars"`` — variables that do not fit the above types.
            - ``"extraction_time"`` — Unix timestamp of extraction.
        """
        bool_vars: list[str] = []
        int_vars: list[str] = []
        str_vars: list[str] = []
        other_vars: list[str] = []

        for key, val in model_dict.items():
            if isinstance(val, bool):
                # Note: check bool before int because bool is a subclass of int.
                bool_vars.append(key)
            elif isinstance(val, int):
                int_vars.append(key)
            elif isinstance(val, str):
                str_vars.append(key)
            else:
                other_vars.append(key)

        return {
            "assignments": model_dict,
            "bool_vars": bool_vars,
            "int_vars": int_vars,
            "str_vars": str_vars,
            "other_vars": other_vars,
            "extraction_time": time.time(),
        }

    def run_assembly(self, extracted: dict[str, Any]) -> dict[str, Any]:
        """Phase 2 — Assemble extracted assignments into prefix-grouped buckets.

        Variable names are split on ``"_"`` and the first component is used
        as the group key.  This heuristic works well for JuGeo's naming
        convention where variables are prefixed with their enclosing
        judgment or sort name.

        Parameters
        ----------
        extracted:
            The output of :meth:`run_extraction`.

        Returns
        -------
        dict[str, Any]
            A structured assembly result with the following keys:

            - ``"assembled"`` — dict mapping group prefix → sub-dict of
              ``{var: value}`` pairs.
            - ``"group_count"`` — number of distinct groups.
            - ``"assembly_time"`` — Unix timestamp.
        """
        assignments: dict[str, Any] = extracted.get("assignments", {})
        grouped: dict[str, dict[str, Any]] = {}

        for var, val in assignments.items():
            # Split on underscore and use the first component as group.
            parts = var.split("_", maxsplit=1)
            group = parts[0] if parts else var
            if group not in grouped:
                grouped[group] = {}
            grouped[group][var] = val

        return {
            "assembled": grouped,
            "group_count": len(grouped),
            "assembly_time": time.time(),
        }

    def run_validation(self, assembled: dict[str, Any]) -> list[str]:
        """Phase 3 — Validate the assembled model for basic consistency.

        Checks that the assembled model is non-empty and that none of the
        original assignments carry a ``None`` value (which would indicate
        a decoder failure upstream).

        Parameters
        ----------
        assembled:
            The output of :meth:`run_assembly`.

        Returns
        -------
        list[str]
            List of error strings.  An empty list means the model passed
            all validation checks.
        """
        validation_errors: list[str] = []
        assembled_dict: dict[str, dict[str, Any]] = assembled.get("assembled", {})

        if not assembled_dict:
            validation_errors.append(
                "run_validation: assembled model is empty — no assignments were produced."
            )
            return validation_errors

        # Flatten to (var, val) pairs for None-check.
        for group, sub_dict in assembled_dict.items():
            for var, val in sub_dict.items():
                if val is None:
                    validation_errors.append(
                        f"run_validation: variable '{var}' in group '{group}' "
                        f"has a None value — possible decoder failure."
                    )

        return validation_errors

    def run_trust_annotation(
        self,
        validated: dict[str, Any],
        trust_kind: str,
    ) -> dict[str, Any]:
        """Phase 4 — Annotate all assignments with a uniform trust level.

        Adds ``trust_kind`` to the assembled model so that downstream
        consumers know the epistemic status of each assignment.

        Parameters
        ----------
        validated:
            The output of :meth:`run_assembly` (passed through validation).
        trust_kind:
            A trust level string from the JuGeo trust algebra, e.g.
            ``"SOLVER_INFERRED"`` or ``"AUTOMATED"``.

        Returns
        -------
        dict[str, Any]
            A shallow copy of *validated* extended with:

            - ``"trust_kind"``   — the supplied *trust_kind* string.
            - ``"annotated_at"`` — Unix timestamp.
        """
        annotated: dict[str, Any] = dict(validated)
        annotated["trust_kind"] = trust_kind
        annotated["annotated_at"] = time.time()
        return annotated

    def run_packaging(self, annotated: dict[str, Any]) -> dict[str, Any]:
        """Phase 5 — Wrap the annotated model in a signed evidence envelope.

        The package includes the pipeline's own identifier so that the
        provenance of each evidence fragment can be traced back to the
        exact pipeline run that produced it.

        Parameters
        ----------
        annotated:
            The output of :meth:`run_trust_annotation`.

        Returns
        -------
        dict[str, Any]
            The final evidence envelope with keys:

            - ``"pipeline_id"``  — this pipeline's UUID.
            - ``"phase"``        — always ``"complete"``.
            - ``"evidence"``     — the *annotated* dict.
            - ``"packaged_at"``  — Unix timestamp.
            - ``"error_count"``  — number of errors recorded.
        """
        return {
            "pipeline_id": self.pipeline_id,
            "phase": "complete",
            "evidence": annotated,
            "packaged_at": time.time(),
            "error_count": len(self.errors),
        }

    def full_run(
        self,
        model_dict: dict[str, Any],
        trust_kind: str,
    ) -> dict[str, Any]:
        """Execute all five pipeline phases in sequence.

        This convenience method chains extraction → assembly → validation
        → trust annotation → packaging and returns the final packaged
        evidence dict.  Validation errors are recorded via
        :meth:`record_error` but do not abort the run; the package will
        contain the ``error_count`` so consumers can decide how to handle
        partial failures.

        Parameters
        ----------
        model_dict:
            Raw Z3 model dictionary to reconstruct.
        trust_kind:
            Trust level string to apply during annotation.

        Returns
        -------
        dict[str, Any]
            The final evidence package from :meth:`run_packaging`.
        """
        # ── Phase 1: Extraction ────────────────────────────────────────
        self.current_phase = AssemblyPhase.EXTRACTION
        extracted = self.run_extraction(model_dict)
        self.record_result("extraction", extracted)
        self.advance_phase()

        # ── Phase 2: Assembly ──────────────────────────────────────────
        assembled = self.run_assembly(extracted)
        self.record_result("assembly", assembled)
        self.advance_phase()

        # ── Phase 3: Validation ────────────────────────────────────────
        validation_errors = self.run_validation(assembled)
        for err in validation_errors:
            self.record_error(err)
        self.record_result("validation_errors", validation_errors)
        self.advance_phase()

        # ── Phase 4: Trust Annotation ──────────────────────────────────
        annotated = self.run_trust_annotation(assembled, trust_kind)
        self.record_result("trust_annotation", annotated)
        self.advance_phase()

        # ── Phase 5: Packaging ─────────────────────────────────────────
        packaged = self.run_packaging(annotated)
        self.record_result("packaging", packaged)
        self.advance_phase()  # → COMPLETE

        return packaged


# ---------------------------------------------------------------------------
# §31.4.2 — PartialModelAssembler
# ---------------------------------------------------------------------------


@dataclass
class PartialModelAssembler:
    """Assembler for partial Z3 models with configurable completion strategies.

    A :class:`PartialModelAssembler` tracks a *partial assignment*: a
    mapping from variable names to their values, alongside a set of
    *undefined variables* — variables that are in scope but have not yet
    been assigned a value by Z3.

    The assembler supports several strategies for completing the model
    (§31.4.2), converting a partial model into a total one by supplying
    default values for all undefined variables.

    Parameters
    ----------
    assembler_id:
        UUID string uniquely identifying this assembler instance.
    partial_assignments:
        The current partial assignment mapping.
    completion_strategy:
        Strategy to use when completing the model.
    undefined_vars:
        Set of variable names known to be in scope but without assignments.

    Notes
    -----
    The left-biased join operation :math:`M_1 \\sqcup M_2` is implemented
    by :meth:`merge_with`.
    """

    assembler_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    partial_assignments: dict[str, Any] = field(default_factory=dict)
    completion_strategy: CompletionStrategy = CompletionStrategy.CONSERVATIVE
    undefined_vars: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------
    # Assignment management
    # ------------------------------------------------------------------

    def add_assignment(self, var: str, value: Any) -> None:
        """Record that variable *var* has been assigned *value*.

        If *var* was previously in :attr:`undefined_vars`, it is removed
        from that set upon assignment (it is now defined).

        Parameters
        ----------
        var:
            Variable name.
        value:
            The value assigned to *var* by the Z3 model.
        """
        self.partial_assignments[var] = value
        self.undefined_vars.discard(var)  # remove if present

    def mark_undefined(self, var: str) -> None:
        """Register *var* as in-scope but without an assigned value.

        A variable that already has an assignment in
        :attr:`partial_assignments` is *not* added to
        :attr:`undefined_vars`; it would be misleading to mark a defined
        variable as undefined.

        Parameters
        ----------
        var:
            Variable name to mark as undefined.
        """
        if var not in self.partial_assignments:
            self.undefined_vars.add(var)

    def is_complete_model(self) -> bool:
        """Return ``True`` iff all in-scope variables have been assigned.

        A model is complete when :attr:`undefined_vars` is empty.

        Returns
        -------
        bool
            ``True`` iff ``len(self.undefined_vars) == 0``.
        """
        return len(self.undefined_vars) == 0

    # ------------------------------------------------------------------
    # Completion strategies
    # ------------------------------------------------------------------

    def complete_conservatively(
        self,
        sort_defaults: dict[str, Any],
    ) -> dict[str, Any]:
        """Complete the partial model using conservative (minimal) defaults.

        For each undefined variable, the assembler attempts to infer the
        variable's sort from the *sort_defaults* dictionary (which maps
        sort names to default values).  If no sort hint is available, it
        falls back to Python-level defaults: ``0`` for ``Int``, ``False``
        for ``Bool``, and ``""`` for ``String``.

        Parameters
        ----------
        sort_defaults:
            Mapping from sort names to conservative default values.

        Returns
        -------
        dict[str, Any]
            The completed assignment dict (does not mutate
            :attr:`partial_assignments`).
        """
        completed: dict[str, Any] = dict(self.partial_assignments)

        for var in self.undefined_vars:
            # Attempt to infer sort from the variable name convention.
            # JuGeo names variables as "{sort}_{suffix}", so split on "_".
            sort_hint = var.split("_")[0] if "_" in var else ""

            if sort_hint in sort_defaults:
                # Use the caller-supplied sort default.
                completed[var] = sort_defaults[sort_hint]
            elif sort_hint.lower() in ("int", "integer", "nat", "natural"):
                completed[var] = 0
            elif sort_hint.lower() in ("bool", "boolean"):
                completed[var] = False
            elif sort_hint.lower() in ("string", "str"):
                completed[var] = ""
            else:
                # Generic conservative default: zero integer.
                completed[var] = 0

        return completed

    def complete_liberally(
        self,
        universe: dict[str, list[Any]],
    ) -> dict[str, Any]:
        """Complete the partial model by picking the first universe element.

        For each undefined variable, the assembler looks up the variable's
        sort in *universe* and assigns the first element of that sort's
        enumeration.  If the sort is not found, ``None`` is used (and
        should be treated as an error by validation).

        Parameters
        ----------
        universe:
            Mapping from sort name to a list of known values for that sort.

        Returns
        -------
        dict[str, Any]
            The completed assignment dict.
        """
        completed: dict[str, Any] = dict(self.partial_assignments)

        for var in self.undefined_vars:
            # Infer sort from variable name prefix (see conservative method).
            sort_hint = var.split("_")[0] if "_" in var else var

            if sort_hint in universe and universe[sort_hint]:
                # Use the first (smallest-index) element of the universe.
                completed[var] = universe[sort_hint][0]
            else:
                # Sort not in universe — fall back to None as a signal.
                completed[var] = None

        return completed

    def copilot_complete(self, hints: dict[str, str]) -> dict[str, Any]:
        """Complete the partial model using Copilot-supplied string hints.

        For each undefined variable, the *hints* dictionary is checked for
        an entry keyed by the variable name.  When found, the hint string
        is used as the value.  When absent, the variable is assigned the
        literal string ``"<copilot_no_hint>"`` as a sentinel.

        Parameters
        ----------
        hints:
            Mapping from variable name to a string hint value.

        Returns
        -------
        dict[str, Any]
            The completed assignment dict with string-typed values for all
            previously-undefined variables.
        """
        completed: dict[str, Any] = dict(self.partial_assignments)

        for var in self.undefined_vars:
            if var in hints:
                # Use the Copilot-supplied hint verbatim.
                completed[var] = hints[var]
            else:
                # Sentinel value indicating no hint was available.
                completed[var] = "<copilot_no_hint>"

        return completed

    # ------------------------------------------------------------------
    # Merge (§31.4.2 left-biased join)
    # ------------------------------------------------------------------

    def merge_with(self, other: PartialModelAssembler) -> PartialModelAssembler:
        """Return a new assembler that is the left-biased join with *other*.

        The join :math:`M_1 \\sqcup M_2` (where ``self`` is :math:`M_1`)
        is realized by:

        1. Starting with a copy of ``self.partial_assignments``.
        2. Adding all entries from ``other.partial_assignments`` that are
           *absent* from ``self.partial_assignments``.
        3. Computing the new undefined set as the union of both undefined
           sets, minus all variables now defined.

        Parameters
        ----------
        other:
            The assembler to merge into ``self`` (with lower precedence).

        Returns
        -------
        PartialModelAssembler
            A fresh assembler instance representing the merged model.
        """
        # Left-biased union: self wins on conflicts.
        merged_assignments: dict[str, Any] = dict(other.partial_assignments)
        merged_assignments.update(self.partial_assignments)  # self overwrites other

        # New undefined = union of both undefined sets, minus now-defined vars.
        merged_undefined = (self.undefined_vars | other.undefined_vars) - set(
            merged_assignments.keys()
        )

        return PartialModelAssembler(
            assembler_id=str(uuid.uuid4()),
            partial_assignments=merged_assignments,
            completion_strategy=self.completion_strategy,
            undefined_vars=merged_undefined,
        )

    # ------------------------------------------------------------------
    # Serialization / evidence
    # ------------------------------------------------------------------

    def to_evidence_fragment(self) -> dict[str, Any]:
        """Serialize the current partial model as an evidence fragment.

        Returns
        -------
        dict[str, Any]
            Dictionary suitable for inclusion in an evidence package,
            containing the assembler's identity, assignments,
            undefined-count, completeness status, and strategy.
        """
        return {
            "assembler_id": self.assembler_id,
            "partial_assignments": self.partial_assignments,
            "undefined_count": len(self.undefined_vars),
            "is_complete": self.is_complete_model(),
            "strategy": self.completion_strategy.value,
        }

    # ------------------------------------------------------------------
    # Type validation
    # ------------------------------------------------------------------

    def validate_types(self, type_env: dict[str, str]) -> list[str]:
        """Check that assigned values match expected SMT-LIB 2 sort types.

        Validates each variable in :attr:`partial_assignments` against the
        declared sort in *type_env*.  Type consistency rules:

        - Sort ``"Int"``    → Python value must be ``int`` (not ``bool``).
        - Sort ``"Bool"``   → Python value must be ``bool``.
        - Sort ``"String"`` → Python value must be ``str``.

        Parameters
        ----------
        type_env:
            Mapping from variable name to expected sort name.

        Returns
        -------
        list[str]
            One error string per type mismatch.  Empty list on success.
        """
        errors: list[str] = []

        for var, val in self.partial_assignments.items():
            if var not in type_env:
                # No type information available — skip.
                continue

            expected_sort = type_env[var]

            if expected_sort == "Int":
                # bool is a subclass of int in Python; reject it here.
                if not isinstance(val, int) or isinstance(val, bool):
                    errors.append(
                        f"validate_types: variable '{var}' has sort 'Int' "
                        f"but value {val!r} is not an integer."
                    )
            elif expected_sort == "Bool":
                if not isinstance(val, bool):
                    errors.append(
                        f"validate_types: variable '{var}' has sort 'Bool' "
                        f"but value {val!r} is not a boolean."
                    )
            elif expected_sort == "String":
                if not isinstance(val, str):
                    errors.append(
                        f"validate_types: variable '{var}' has sort 'String' "
                        f"but value {val!r} is not a string."
                    )
            # Other sorts are not checked at the Python level.

        return errors


# ---------------------------------------------------------------------------
# §31.4.3 — TrustAnnotator
# ---------------------------------------------------------------------------


@dataclass
class TrustAnnotator:
    """Annotation manager that tracks trust levels for model assignments.

    The :class:`TrustAnnotator` maintains a mapping from assignment keys
    to trust-level strings drawn from the JuGeo trust algebra.  It
    supports downgrading (to enforce a ceiling), merging (with left-bias),
    and querying the highest or lowest trust level present.

    Trust levels, in ascending order of epistemic confidence:

    1. ``UNVERIFIED``       — source of assignment is unknown.
    2. ``AUTOMATED``        — assigned by an automated script, no review.
    3. ``COPILOT_PROPOSED`` — proposed by the Copilot subsystem.
    4. ``SOLVER_INFERRED``  — inferred by the Z3 SMT solver.
    5. ``HUMAN_REVIEWED``   — reviewed and approved by a human.

    Parameters
    ----------
    annotator_id:
        UUID string uniquely identifying this annotator instance.
    annotations:
        Mapping from key (variable name or assignment identifier) to
        trust level string.
    default_trust:
        The trust level returned for keys not present in :attr:`annotations`.
    """

    annotator_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    annotations: dict[str, str] = field(default_factory=dict)
    default_trust: str = "UNVERIFIED"

    # ------------------------------------------------------------------
    # Annotation management
    # ------------------------------------------------------------------

    def annotate(self, key: str, trust_level: str) -> None:
        """Record that *key* has trust level *trust_level*.

        Parameters
        ----------
        key:
            The variable name or assignment identifier to annotate.
        trust_level:
            A trust level string; should be one of the five levels in
            ``_TRUST_ORDER`` but is not validated here to allow extension.
        """
        self.annotations[key] = trust_level

    def annotate_all(self, keys: list[str], trust_level: str) -> None:
        """Annotate every key in *keys* with the same *trust_level*.

        This bulk method is useful when an entire phase's outputs share
        the same epistemic provenance (e.g. all Z3-inferred assignments
        get ``SOLVER_INFERRED``).

        Parameters
        ----------
        keys:
            List of keys to annotate.
        trust_level:
            The trust level to apply uniformly.
        """
        for key in keys:
            self.annotate(key, trust_level)

    def lookup(self, key: str) -> str:
        """Return the trust level for *key*, or the default if not annotated.

        Parameters
        ----------
        key:
            The key to look up.

        Returns
        -------
        str
            The recorded trust level, or :attr:`default_trust` if absent.
        """
        return self.annotations.get(key, self.default_trust)

    # ------------------------------------------------------------------
    # Trust-order operations
    # ------------------------------------------------------------------

    def downgrade_all_to(self, ceiling: str) -> None:
        """Downgrade all annotations that exceed *ceiling* to *ceiling*.

        The trust order is defined by :data:`_TRUST_ORDER`.  Any key
        whose current trust level is strictly higher than *ceiling* (i.e.
        has a larger index in ``_TRUST_ORDER``) is downgraded to *ceiling*.

        Parameters
        ----------
        ceiling:
            The maximum allowable trust level.  Annotations at or below
            this level are left unchanged.
        """
        if ceiling not in _TRUST_ORDER:
            # Unknown ceiling — do nothing rather than corrupting annotations.
            return

        ceiling_idx = _TRUST_ORDER.index(ceiling)

        for key, level in list(self.annotations.items()):
            if level not in _TRUST_ORDER:
                # Unknown level — leave it unchanged (extension point).
                continue
            if _TRUST_ORDER.index(level) > ceiling_idx:
                # This level exceeds the ceiling; downgrade it.
                self.annotations[key] = ceiling

    def highest_trust(self) -> str:
        """Return the highest trust level currently present in annotations.

        Iterates over all recorded trust levels and returns the one with
        the largest index in :data:`_TRUST_ORDER`.  If no annotations are
        present, :attr:`default_trust` is returned.

        Returns
        -------
        str
            The highest trust level string found, or ``default_trust``.
        """
        if not self.annotations:
            return self.default_trust

        best_idx = -1
        best_level = self.default_trust

        for level in self.annotations.values():
            if level in _TRUST_ORDER:
                idx = _TRUST_ORDER.index(level)
                if idx > best_idx:
                    best_idx = idx
                    best_level = level

        return best_level

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge_annotations(self, other: TrustAnnotator) -> TrustAnnotator:
        """Return a new annotator that is the left-biased merge with *other*.

        Entries in ``self`` take precedence over entries in ``other`` for
        any key present in both.

        Parameters
        ----------
        other:
            The annotator to merge with lower priority.

        Returns
        -------
        TrustAnnotator
            A fresh :class:`TrustAnnotator` instance.
        """
        merged: dict[str, str] = dict(other.annotations)
        merged.update(self.annotations)  # self wins on conflict
        return TrustAnnotator(
            annotator_id=str(uuid.uuid4()),
            annotations=merged,
            default_trust=self.default_trust,
        )

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def is_fully_annotated(self, keys: list[str]) -> bool:
        """Return ``True`` iff every key in *keys* has been explicitly annotated.

        Parameters
        ----------
        keys:
            The list of keys to check.

        Returns
        -------
        bool
            ``True`` when all keys appear in :attr:`annotations`.
        """
        return all(k in self.annotations for k in keys)

    def summary(self) -> dict[str, Any]:
        """Return a statistics summary of this annotator's state.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys:

            - ``"annotator_id"``       — this instance's UUID.
            - ``"total_annotations"``  — number of keys annotated.
            - ``"default_trust"``      — the default trust level.
            - ``"trust_distribution"`` — mapping from trust level to count.
        """
        distribution: dict[str, int] = {}
        for level in self.annotations.values():
            distribution[level] = distribution.get(level, 0) + 1

        return {
            "annotator_id": self.annotator_id,
            "total_annotations": len(self.annotations),
            "default_trust": self.default_trust,
            "trust_distribution": distribution,
        }


# ---------------------------------------------------------------------------
# §31.4.4 — EvidencePackager
# ---------------------------------------------------------------------------


@dataclass
class EvidencePackager:
    """Packager that collects, signs, and finalizes evidence fragments.

    An :class:`EvidencePackager` is the last stage in the reconstruction
    pipeline.  It collects one or more evidence dictionaries, optionally
    signs each one with a SHA-256 digest, and produces a final sealed
    evidence envelope that can be stored in the JuGeo evidence database.

    Parameters
    ----------
    packager_id:
        UUID string uniquely identifying this packager instance.
    packages:
        The list of evidence fragment dictionaries collected so far.
    query_id:
        The identifier of the JuGeo query that initiated reconstruction.
        Used to link evidence back to the originating query.
    """

    packager_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    packages: list[dict[str, Any]] = field(default_factory=list)
    query_id: str = ""

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    def add_package(self, evidence: dict[str, Any]) -> None:
        """Append an evidence fragment to the package list.

        The ``"package_index"`` key is injected into *evidence* before
        appending so that each fragment carries its own position in the
        list.  This makes it easy to retrieve specific fragments by index
        later.

        Parameters
        ----------
        evidence:
            A non-empty evidence fragment dictionary.
        """
        # Record the position before appending.
        index = len(self.packages)
        evidence_with_index = dict(evidence)
        evidence_with_index["package_index"] = index
        self.packages.append(evidence_with_index)

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    def finalize(self) -> dict[str, Any]:
        """Seal all collected packages into a final evidence envelope.

        The envelope includes a SHA-256 digest of the entire packages list
        so that downstream consumers can detect any tampering or accidental
        mutation.

        Returns
        -------
        dict[str, Any]
            Sealed evidence envelope with keys:

            - ``"packager_id"``   — this packager's UUID.
            - ``"query_id"``      — the originating query identifier.
            - ``"packages"``      — the list of evidence fragments.
            - ``"total_packages"`` — count of fragments.
            - ``"finalized_at"``  — Unix timestamp.
            - ``"package_hash"``  — SHA-256 hex digest of the packages list.
        """
        # Compute a deterministic hash of the packages list.
        packages_json = json.dumps(self.packages, sort_keys=True, default=str)
        package_hash = hashlib.sha256(packages_json.encode()).hexdigest()

        return {
            "packager_id": self.packager_id,
            "query_id": self.query_id,
            "packages": self.packages,
            "total_packages": len(self.packages),
            "finalized_at": time.time(),
            "package_hash": package_hash,
        }

    def sign_package(self, package: dict[str, Any]) -> dict[str, Any]:
        """Return a signed copy of *package* with a SHA-256 digest appended.

        The signature is computed over the JSON serialization of the
        package (excluding the signature itself, since it is not yet
        present when the hash is computed).

        Parameters
        ----------
        package:
            The evidence fragment to sign.

        Returns
        -------
        dict[str, Any]
            A shallow copy of *package* with additional keys:

            - ``"signature"``  — SHA-256 hex digest.
            - ``"signed_at"``  — Unix timestamp.
        """
        # Serialize the original package to produce the signature input.
        package_json = json.dumps(package, sort_keys=True, default=str)
        signature = hashlib.sha256(package_json.encode()).hexdigest()

        signed_copy: dict[str, Any] = dict(package)
        signed_copy["signature"] = signature
        signed_copy["signed_at"] = time.time()
        return signed_copy

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_packages(self) -> list[str]:
        """Return error messages for any malformed packages in the list.

        A package is considered malformed if it is ``None``, not a dict,
        or an empty dict.

        Returns
        -------
        list[str]
            One error string per invalid package, including the package
            index for easy debugging.  Empty on success.
        """
        errors: list[str] = []

        for idx, pkg in enumerate(self.packages):
            if not isinstance(pkg, dict):
                errors.append(
                    f"validate_packages: package at index {idx} is not a dict "
                    f"(got {type(pkg).__name__})."
                )
            elif not pkg:
                errors.append(
                    f"validate_packages: package at index {idx} is an empty dict."
                )

        return errors

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge_packages(self, other: EvidencePackager) -> EvidencePackager:
        """Return a new packager whose package list combines both inputs.

        The merged packager contains all packages from ``self`` followed
        by all packages from ``other``.  The ``query_id`` from ``self``
        takes precedence.

        Parameters
        ----------
        other:
            The packager whose packages are appended (with lower priority).

        Returns
        -------
        EvidencePackager
            A fresh :class:`EvidencePackager` with the combined list.
        """
        combined_packages = list(self.packages) + list(other.packages)
        return EvidencePackager(
            packager_id=str(uuid.uuid4()),
            packages=combined_packages,
            query_id=self.query_id if self.query_id else other.query_id,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialize this packager to a JSON string.

        Returns
        -------
        str
            Indented JSON representation of the packager's state,
            including ``packager_id``, ``query_id``, and ``packages``.
        """
        state: dict[str, Any] = {
            "packager_id": self.packager_id,
            "query_id": self.query_id,
            "packages": self.packages,
        }
        return json.dumps(state, indent=2, default=str)

    @classmethod
    def from_json(cls, s: str) -> EvidencePackager:
        """Deserialize an :class:`EvidencePackager` from a JSON string.

        Parameters
        ----------
        s:
            A JSON string previously produced by :meth:`to_json`.

        Returns
        -------
        EvidencePackager
            A reconstructed packager instance with the same state.

        Raises
        ------
        json.JSONDecodeError
            When *s* is not valid JSON.
        KeyError
            When required keys are absent from the JSON object.
        """
        data: dict[str, Any] = json.loads(s)
        return cls(
            packager_id=data["packager_id"],
            packages=data.get("packages", []),
            query_id=data.get("query_id", ""),
        )


# ---------------------------------------------------------------------------
# §31.4 — Module-level convenience functions
# ---------------------------------------------------------------------------


def run_full_reconstruction(
    model_dict: dict[str, Any],
    query_id: str,
    trust_kind: str,
) -> dict[str, Any]:
    """Run the complete five-phase reconstruction pipeline on *model_dict*.

    This is the primary entry point for external callers.  It creates a
    fresh :class:`ReconstructionPipeline`, executes all phases, and
    returns the final evidence package.

    Parameters
    ----------
    model_dict:
        The raw Z3 model dictionary to reconstruct.
    query_id:
        The JuGeo query identifier; recorded in the pipeline metadata.
    trust_kind:
        Trust level string for annotation (e.g. ``"SOLVER_INFERRED"``).

    Returns
    -------
    dict[str, Any]
        The final packaged evidence dictionary from the pipeline.
    """
    pipeline = ReconstructionPipeline()
    pipeline.metadata["query_id"] = query_id
    result = pipeline.full_run(model_dict, trust_kind)
    return result


def assemble_from_partial_models(
    models: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge a list of partial model dictionaries into a single evidence fragment.

    All assignments from all models are loaded into a single
    :class:`PartialModelAssembler` using left-to-right priority (earlier
    models win on conflict), and the resulting evidence fragment is
    returned.

    Parameters
    ----------
    models:
        List of partial model dictionaries, each mapping variable names
        to their assigned values.

    Returns
    -------
    dict[str, Any]
        An evidence fragment produced by
        :meth:`PartialModelAssembler.to_evidence_fragment`.
    """
    assembler = PartialModelAssembler()

    # Process models in order; add_assignment will silently overwrite
    # with later models — to achieve left-priority, reverse the list so
    # the first model's assignments are written last and thus win.
    for model in reversed(models):
        for var, val in model.items():
            assembler.add_assignment(var, val)

    return assembler.to_evidence_fragment()


def annotate_with_trust(
    evidence: dict[str, Any],
    trust_kind: str,
) -> dict[str, Any]:
    """Add trust-level annotations to every key in *evidence*.

    Creates a :class:`TrustAnnotator`, annotates all keys with
    *trust_kind*, and returns an updated copy of *evidence* that includes
    a ``"trust_annotations"`` sub-dictionary.

    Parameters
    ----------
    evidence:
        The evidence dictionary whose keys should be annotated.
    trust_kind:
        The trust level string to apply uniformly.

    Returns
    -------
    dict[str, Any]
        A shallow copy of *evidence* extended with:

        - ``"trust_annotations"`` — mapping from each key to *trust_kind*.
        - ``"trust_summary"``     — the annotator's :meth:`~TrustAnnotator.summary`.
    """
    annotator = TrustAnnotator(default_trust=trust_kind)
    all_keys = list(evidence.keys())
    annotator.annotate_all(all_keys, trust_kind)

    updated: dict[str, Any] = dict(evidence)
    updated["trust_annotations"] = dict(annotator.annotations)
    updated["trust_summary"] = annotator.summary()
    return updated


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "AssemblyPhase",
    "CompletionStrategy",
    "ReconstructionPipeline",
    "PartialModelAssembler",
    "TrustAnnotator",
    "EvidencePackager",
    "run_full_reconstruction",
    "assemble_from_partial_models",
    "annotate_with_trust",
]
