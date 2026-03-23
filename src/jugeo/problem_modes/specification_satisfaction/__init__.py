"""specification_satisfaction -- JuGeo problem mode for Theory 2, Chapter 10.

A specification is a target section of the judgment sheaf: a prescription
of what judgments should hold globally over the project site.  Satisfaction
is descent: showing that local evidence glues to match the global specification.

This package implements the full specification-satisfaction problem mode described
in theory2.tex Ch10.  The core objects are:

* :class:`Specification` -- the prescription (S10.1)
* :class:`SatisfactionWitness` -- the local evidence + gluing data (S10.2)
* :class:`CertificateOfSatisfaction` -- the settled certificate (S10.3)
* :class:`ResidualGap` -- what remains unresolved when descent partially fails (S10.4)

The package is organized into sections matching the theory:

* ``models`` -- core domain models
* ``specifications`` -- specification construction and composition (S10.1)
* ``satisfaction_witnesses`` -- witness construction and validation (S10.2)
* ``descent_conditions`` -- descent condition checking and global section
  extraction (S10.3)
* ``residual_gaps`` -- gap analysis, obstruction classes, and repair
  strategies (S10.4)
* ``algorithms`` -- core satisfaction algorithms
* ``integration`` -- integration with other jugeo subsystems
* ``theorems`` -- formal theorem statements
* ``manifest`` -- package manifest and module registry

copilot: shared-core module -- every public surface is designed for LLM
orchestration and Copilot-assisted verification workflows.

References
----------
theory2.tex S10.1   "Specifications"
theory2.tex S10.2   "Satisfaction Witnesses"
theory2.tex S10.3   "Descent and Certificates"
theory2.tex S10.4   "Residual Gaps"
theory2.tex S10.5   "Composition"

Overview
--------
The *specification-satisfaction* problem mode is the formal mechanism by which
JuGeo decides whether a software project *meets* a given specification.
Concretely:

1. A :class:`Specification` is constructed (S10.1).  It prescribes, for each
   *coordinate* in the project site, a *local judgment* that the artefact at
   that coordinate must satisfy -- e.g., "this function must be type-correct",
   "this API endpoint must match its OpenAPI schema", "this module must pass
   its unit-test suite".

2. A :class:`SatisfactionWitness` (S10.2) collects the *local evidence* for
   each coordinate.  For a type-checking coordinate the evidence is the output
   of the type-checker; for a test coordinate it is the test-run report.  The
   witness also carries *gluing data* -- the compatibility proofs that ensure
   local evidence pieces agree on overlapping portions of the site.

3. The *descent* step (S10.3) checks whether the local evidence genuinely glues
   to a global section of the judgment sheaf.  Abstractly this means verifying
   that the Cech 1-cocycle formed by the gluing data is cohomologically trivial.
   In practice the :func:`run_satisfaction_descent` function orchestrates this
   check.

4. If descent succeeds, a :class:`CertificateOfSatisfaction` is issued (S10.3).
   The certificate records the global section, the trust score derived from
   the evidence, and a cryptographic digest for later auditing.

5. If descent partially fails, a :class:`ResidualGap` is computed (S10.4).
   The gap records exactly which coordinates remain unresolved and carries
   enough information to drive a repair loop: the :func:`generate_repair_strategy`
   function produces a prioritised list of repair actions.

6. The composition theorem (S10.5, formalised as
   :data:`theorem_composition_satisfaction`) guarantees that separate
   satisfaction results for sub-specifications can be merged into a single
   certificate for the composed specification, provided the witnesses are
   mutually compatible.

Design Philosophy
-----------------
Every public object in this package is designed to be:

* **Immutable by default** -- the core domain objects (:class:`Specification`,
  :class:`SatisfactionWitness`, etc.) use ``@dataclass(frozen=True)`` so they
  can be safely passed to concurrent workers and LLM orchestration loops
  without fear of mutation.
* **Serialisable** -- every object exposes a ``to_dict()`` / ``from_dict()``
  pair for JSON round-tripping, supporting Copilot audit trails and cross-agent
  communication.
* **Composable** -- the :func:`compose_specifications`,
  :func:`merge_witnesses`, and
  :func:`specification_composition_algorithm` functions let you build complex
  specifications from simple ones and combine their witnesses.
* **Auditable** -- the :class:`CertificateOfSatisfaction` carries a
  cryptographic digest and the :class:`ProofVerifier` records a full audit log.

LLM Integration Notes
---------------------
When using this package inside an LLM orchestration loop:

* Use :func:`satisfy` as the single entry point: pass a :class:`Specification`
  and an ``evidence_map`` and receive a certificate or partial witness.
* Use :func:`quick_check` for a simple boolean answer.
* Use :func:`get_gaps` to inspect what is still missing after a partial
  satisfaction attempt.
* Use :func:`build_spec` to construct a specification from a template and
  constraint list.
* Use :func:`run_descent` to run the sheaf-theoretic descent step in
  isolation (useful when evidence has already been collected).

All functions handle ``ImportError`` gracefully -- if a sub-module has not yet
been implemented the corresponding function falls back to a descriptive
``NotImplementedError`` with a helpful message.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
try:
    from jugeo.problem_modes.specification_satisfaction.models import (
        SpecificationKind,
        WitnessStatus,
        GapSeverity,
        SatisfactionStatus,
        DescentCondition,
        Specification,
        SatisfactionWitness,
        CertificateOfSatisfaction,
        ResidualGap,
    )
except ImportError:
    pass

# ---------------------------------------------------------------------------
# s01 specifications
# ---------------------------------------------------------------------------
try:
    from jugeo.problem_modes.specification_satisfaction.specifications import (
        SpecificationBuilder,
        ConstraintEncoder,
        SpecificationNormalizer,
        SpecificationComposer,
        GlobalSectionPrescription,
        build_specification,
        parse_constraint_list,
        compose_specifications,
        specification_from_template,
        validate_specification,
        TYPE_SAFE_TEMPLATE,
        BEHAVIOR_CORRECT_TEMPLATE,
        API_CONSISTENT_TEMPLATE,
    )
except ImportError:
    pass

# ---------------------------------------------------------------------------
# s02 satisfaction witnesses
# ---------------------------------------------------------------------------
try:
    from jugeo.problem_modes.specification_satisfaction.satisfaction_witnesses import (
        WitnessBuilder,
        EvidenceCollector,
        GluingDataComputer,
        WitnessMerger,
        WitnessValidator,
        build_witness,
        collect_evidence_for_spec,
        compute_gluing_data,
        merge_witnesses,
        validate_witness,
    )
except ImportError:
    pass

# ---------------------------------------------------------------------------
# s03 descent conditions
# ---------------------------------------------------------------------------
try:
    from jugeo.problem_modes.specification_satisfaction.descent_conditions import (
        DescentConditionChecker,
        OverlapCompatibilityVerifier,
        GlobalSectionExtractor,
        CocycleComputer,
        DescentOrchestrator,
        check_descent_conditions,
        extract_global_section,
        run_satisfaction_descent,
        compute_cech_cocycle,
        is_descent_possible,
    )
except ImportError:
    pass

# ---------------------------------------------------------------------------
# s04 residual gaps
# ---------------------------------------------------------------------------
try:
    from jugeo.problem_modes.specification_satisfaction.residual_gaps import (
        GapAnalyzer,
        ObstructionClassComputer,
        RepairStrategyEngine,
        GapPrioritizer,
        GapTracker,
        analyze_gaps,
        compute_obstruction,
        generate_repair_strategy,
        prioritize_gaps,
        track_gap_resolution,
    )
except ImportError:
    pass

# ---------------------------------------------------------------------------
# algorithms
# ---------------------------------------------------------------------------
try:
    from jugeo.problem_modes.specification_satisfaction.algorithms import (
        SatisfactionAlgorithmResult,
        IterationState,
        TrustPropagator,
        SpecificationCompositionAlgorithm,
        ResidualMinimizer,
        specification_satisfaction_algorithm,
        descent_for_satisfaction,
        gap_repair_algorithm,
        iterative_satisfaction_loop,
        trust_propagation_for_satisfaction,
        specification_composition_algorithm,
        residual_minimization_algorithm,
    )
except ImportError:
    pass

# ---------------------------------------------------------------------------
# integration
# ---------------------------------------------------------------------------
try:
    from jugeo.problem_modes.specification_satisfaction.integration import (
        SpecificationSatisfactionIntegration,
        SatisfactionExporter,
        SatisfactionImporter,
        SpecificationRegistry,
        SolverConnector,
        register_specification,
        connect_to_solver,
        build_integration,
        export_result_to_json,
        import_specification_from_json,
    )
except ImportError:
    pass

# ---------------------------------------------------------------------------
# theorems
# ---------------------------------------------------------------------------
try:
    from jugeo.problem_modes.specification_satisfaction.theorems import (
        VerificationStatus,
        TheoremCategory,
        Hypothesis,
        TheoremConclusion,
        ProofSketch,
        TheoremStatement,
        TheoremRegistry,
        ProofVerifier,
        theorem_satisfaction_iff_descent,
        theorem_certificate_uniqueness,
        theorem_gap_completeness,
        theorem_monotone_satisfaction,
        theorem_composition_satisfaction,
        get_default_registry,
        get_theorem,
        verify_all_theorems,
        list_theorem_ids,
    )
except ImportError:
    pass

# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
try:
    from jugeo.problem_modes.specification_satisfaction.manifest import (
        PackageManifest,
        ModuleDescriptor,
        get_manifest,
        list_exports,
        validate_package_integrity,
        get_module_descriptor,
        register_module,
        PACKAGE_NAME,
        VERSION,
        AUTHOR,
        CHAPTER,
    )
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def satisfy(
    spec: "Specification",
    evidence_map: dict,
    **kwargs,
) -> "CertificateOfSatisfaction | SatisfactionWitness":
    """Convenience function: attempt to satisfy a specification.

    Runs the full specification-satisfaction pipeline (evidence collection,
    descent, certificate issuance) in a single call.

    Parameters
    ----------
    spec : Specification
        The specification to satisfy.  Must be a well-formed
        :class:`Specification` object (see S10.1 of theory2.tex).
    evidence_map : dict
        Mapping from coordinate identifiers to lists of evidence dictionaries.
        Each evidence dictionary should contain at least a ``"kind"`` key
        and a ``"payload"`` key.  Example::

            evidence_map = {
                "src/utils.py:type_check": [
                    {"kind": "mypy_output", "payload": {"errors": []}}
                ],
                "tests/test_utils.py:test_run": [
                    {"kind": "pytest_output", "payload": {"passed": 42, "failed": 0}}
                ],
            }
    **kwargs
        Additional keyword arguments forwarded to
        :func:`specification_satisfaction_algorithm`.  Common options:

        ``max_iterations`` : int
            Maximum number of repair-and-retry iterations (default: 3).
        ``trust_threshold`` : float
            Minimum trust score to issue a certificate (default: 0.8).

    Returns
    -------
    CertificateOfSatisfaction | SatisfactionWitness
        A :class:`CertificateOfSatisfaction` when descent succeeds, or a
        partial :class:`SatisfactionWitness` when it does not.

    Raises
    ------
    NotImplementedError
        If the ``algorithms`` sub-module is not yet available.

    See Also
    --------
    quick_check : Boolean version of this function.
    get_gaps : Inspect the residual gap after a partial satisfaction attempt.

    Notes
    -----
    This function is the primary entry point for LLM orchestration loops.
    The returned object is always serialisable via ``to_dict()``.
    """
    try:
        return specification_satisfaction_algorithm(spec, evidence_map, **kwargs)  # type: ignore[name-defined]
    except NameError:
        raise NotImplementedError(
            "specification_satisfaction_algorithm is not available -- "
            "ensure jugeo.problem_modes.specification_satisfaction.algorithms "
            "is installed."
        )


def quick_check(spec: "Specification", evidence_map: dict) -> bool:
    """Quick boolean check: is the specification satisfied by this evidence?

    Parameters
    ----------
    spec : Specification
        The specification to check.
    evidence_map : dict
        Evidence map (same format as :func:`satisfy`).

    Returns
    -------
    bool
        ``True`` iff a :class:`CertificateOfSatisfaction` is issued.

    Raises
    ------
    NotImplementedError
        If the ``algorithms`` sub-module is not yet available.

    Examples
    --------
    >>> ok = quick_check(my_spec, {"coord1": [{"kind": "test", "payload": {}}]})
    >>> print("PASS" if ok else "FAIL")
    """
    try:
        result = satisfy(spec, evidence_map)
        return type(result).__name__ == "CertificateOfSatisfaction"
    except NameError:
        raise NotImplementedError(
            "quick_check requires the algorithms sub-module."
        )


def get_gaps(
    spec: "Specification",
    evidence_map: dict,
) -> "ResidualGap | None":
    """Return the residual gap after a (partial) satisfaction attempt.

    Runs the satisfaction pipeline and, if descent fails, computes and returns
    the :class:`ResidualGap` describing which coordinates remain unresolved.

    Parameters
    ----------
    spec : Specification
        The specification being checked.
    evidence_map : dict
        Evidence map (same format as :func:`satisfy`).

    Returns
    -------
    ResidualGap | None
        The residual gap, or ``None`` if the specification is fully satisfied
        (no gap).

    Raises
    ------
    NotImplementedError
        If the ``residual_gaps`` sub-module is not yet available.

    Notes
    -----
    This is the primary entry point for *gap-directed repair loops*.  The
    gap object provides the priority-ordered list of unsatisfied coordinates
    and a suggested repair strategy for each.

    See Also
    --------
    generate_repair_strategy : Produce a repair plan from a residual gap.
    """
    try:
        result = satisfy(spec, evidence_map)
        if type(result).__name__ == "CertificateOfSatisfaction":
            return None
        return analyze_gaps(spec, result)  # type: ignore[name-defined]
    except NameError:
        raise NotImplementedError(
            "get_gaps requires the algorithms and residual_gaps sub-modules."
        )


def build_spec(
    name: str,
    constraints: list,
    kind: str = "behavioral",
    **kwargs,
) -> "Specification":
    """Build a :class:`Specification` from a name and a constraint list.

    Convenience wrapper around :func:`build_specification` that accepts plain
    Python dictionaries as constraints and handles serialisation internally.

    Parameters
    ----------
    name : str
        Human-readable name for the specification.
    constraints : list
        List of constraint dictionaries.  Each dict must contain:

        ``"coordinate"`` : str
            The coordinate identifier.
        ``"judgment"`` : str
            The required judgment (e.g. ``"type_correct"``, ``"test_pass"``).

        Optional keys:

        ``"severity"`` : str
            One of ``"critical"``, ``"major"``, ``"minor"`` (default ``"major"``).
        ``"description"`` : str
            Human-readable description of the constraint.
    kind : str
        Specification kind string -- one of the :class:`SpecificationKind` values.
        Defaults to ``"behavioral"``.
    **kwargs
        Additional kwargs forwarded to :func:`build_specification`.

    Returns
    -------
    Specification
        Constructed specification object.

    Raises
    ------
    NotImplementedError
        If the ``specifications`` sub-module is not yet available.
    """
    try:
        return build_specification(  # type: ignore[name-defined]
            name=name, constraints=constraints, kind=kind, **kwargs
        )
    except NameError:
        raise NotImplementedError(
            "build_spec requires jugeo.problem_modes.specification_satisfaction"
            ".specifications to be installed."
        )


def run_descent(
    spec: "Specification",
    witness: "SatisfactionWitness",
) -> "CertificateOfSatisfaction | ResidualGap":
    """Run the sheaf-theoretic descent step in isolation.

    Assumes that evidence has already been collected into *witness* and performs
    only the descent (S10.3) step: checking overlap compatibility, computing the
    Cech 1-cocycle, and either issuing a certificate or returning a residual gap.

    Parameters
    ----------
    spec : Specification
        The specification being satisfied.
    witness : SatisfactionWitness
        Pre-constructed satisfaction witness.

    Returns
    -------
    CertificateOfSatisfaction | ResidualGap
        Certificate if descent succeeds; residual gap otherwise.

    Raises
    ------
    NotImplementedError
        If the ``descent_conditions`` sub-module is not yet available.

    Notes
    -----
    This function is useful in multi-stage pipelines where evidence collection
    and descent are handled by separate agents.
    """
    try:
        return run_satisfaction_descent(spec, witness)  # type: ignore[name-defined]
    except NameError:
        raise NotImplementedError(
            "run_descent requires jugeo.problem_modes.specification_satisfaction"
            ".descent_conditions to be installed."
        )


def compose(
    spec_a: "Specification",
    spec_b: "Specification",
) -> "Specification":
    """Compose two specifications into a single conjunction specification.

    Implements the composition operation from theory2.tex S10.5: the result
    has coordinate set Coord(S_A) union Coord(S_B) and requires both sets of
    constraints to be satisfied.

    Parameters
    ----------
    spec_a : Specification
        First specification (S_A).
    spec_b : Specification
        Second specification (S_B).

    Returns
    -------
    Specification
        The composed specification S_A and S_B.

    Raises
    ------
    NotImplementedError
        If the ``specifications`` sub-module is not yet available.

    Notes
    -----
    By :data:`theorem_composition_satisfaction`, if spec_a and spec_b are
    individually satisfied by compatible witnesses, the composed specification
    is automatically satisfied by the merged witness.
    """
    try:
        return compose_specifications(spec_a, spec_b)  # type: ignore[name-defined]
    except NameError:
        raise NotImplementedError(
            "compose requires jugeo.problem_modes.specification_satisfaction"
            ".specifications to be installed."
        )


def get_theorems() -> list:
    """Return the list of all canonical Ch10 theorems.

    Retrieves all five theorems from the default registry:

    * ``thm-10-1`` -- Satisfaction iff Descent
    * ``thm-10-2`` -- Certificate Uniqueness
    * ``thm-10-3`` -- Gap Completeness
    * ``thm-10-4`` -- Monotone Satisfaction
    * ``thm-10-5`` -- Composition Satisfaction

    Returns
    -------
    list
        All registered :class:`TheoremStatement` objects in ID order.

    Raises
    ------
    NotImplementedError
        If the ``theorems`` sub-module is not available.
    """
    try:
        reg = get_default_registry()  # type: ignore[name-defined]
        return [reg.get(tid) for tid in reg.list_theorems() if reg.get(tid) is not None]
    except NameError:
        raise NotImplementedError(
            "get_theorems requires jugeo.problem_modes.specification_satisfaction"
            ".theorems to be installed."
        )


def verify_spec(spec: "Specification") -> bool:
    """Validate a specification object for structural well-formedness.

    Checks that the specification has at least one coordinate, all coordinates
    have non-empty judgment prescriptions, and the specification is not marked
    as malformed.

    Parameters
    ----------
    spec : Specification
        The specification to validate.

    Returns
    -------
    bool
        ``True`` iff the specification is well-formed.

    Raises
    ------
    NotImplementedError
        If the ``specifications`` sub-module is not available.
    """
    try:
        return validate_specification(spec)  # type: ignore[name-defined]
    except NameError:
        raise NotImplementedError(
            "verify_spec requires jugeo.problem_modes.specification_satisfaction"
            ".specifications to be installed."
        )


# ---------------------------------------------------------------------------
# Cross-subsystem integration helpers
# ---------------------------------------------------------------------------


def descent_verification(
    spec: "Specification",
    witness: "SatisfactionWitness",
) -> "dict[str, object]":
    """Verify specification satisfaction via sheaf descent using the geometry layer.

    Bridges the specification-satisfaction subsystem to
    :mod:`jugeo.geometry.descent` and :mod:`jugeo.geometry.covers`, performing
    descent on the cover induced by the specification's coordinate set.

    Parameters
    ----------
    spec : Specification
        The specification prescribing local judgments.
    witness : SatisfactionWitness
        Pre-collected local evidence with gluing data.

    Returns
    -------
    dict[str, object]
        Keys: ``descent_result`` (:class:`~jugeo.geometry.descent.DescentResult`
        or ``None``), ``cover`` (the :class:`~jugeo.geometry.covers.Cover` used),
        ``global_section`` (the extracted global section or ``None``),
        ``obstructions`` (list of :class:`~jugeo.geometry.descent.DescentObstruction`).

    Raises
    ------
    NotImplementedError
        If ``jugeo.geometry.descent`` or ``jugeo.geometry.covers`` is unavailable.

    See Also
    --------
    jugeo.geometry.descent.run_descent : Core descent engine.
    jugeo.geometry.covers.Cover : Cover data structure.
    """
    try:
        from jugeo.geometry.descent import DescentEngine, LocalSection
    except ImportError:
        raise NotImplementedError(
            "descent_verification requires jugeo.geometry.descent to be installed."
        )
    try:
        from jugeo.geometry.covers import Cover, CoverBuilder
    except ImportError:
        raise NotImplementedError(
            "descent_verification requires jugeo.geometry.covers to be installed."
        )

    try:
        builder = CoverBuilder()
        coordinates = getattr(spec, "coordinates", None) or []
        for coord in coordinates:
            builder.add_member(str(coord))
        cover = builder.build()
    except Exception:  # noqa: BLE001
        cover = None

    try:
        engine = DescentEngine()
        sections = []
        evidence_map = getattr(witness, "evidence_map", None) or {}
        for coord_id, evidence in evidence_map.items():
            sections.append(LocalSection(
                coordinate_id=str(coord_id),
                data=evidence,
            ))
        descent_result = engine.run(sections, cover=cover)
        global_section = getattr(descent_result, "global_section", None)
        obstructions = getattr(descent_result, "obstructions", [])
    except Exception:  # noqa: BLE001
        descent_result = None
        global_section = None
        obstructions = []

    return {
        "descent_result": descent_result,
        "cover": cover,
        "global_section": global_section,
        "obstructions": list(obstructions),
    }


def judgment_from_spec(
    spec: "Specification",
) -> "list[dict[str, object]]":
    """Create judgment sections from a specification's coordinate prescriptions.

    Converts each coordinate constraint in *spec* into a
    :class:`~jugeo.judgments.sections.JudgmentSection`, bridging the
    specification-satisfaction subsystem to the judgment-section algebra.

    Parameters
    ----------
    spec : Specification
        The specification whose constraints are to be converted.

    Returns
    -------
    list[dict[str, object]]
        One dict per coordinate with keys ``coordinate`` (str),
        ``section`` (:class:`~jugeo.judgments.sections.JudgmentSection`
        or ``None``), and ``error`` (str or ``None``).

    Raises
    ------
    NotImplementedError
        If ``jugeo.judgments.sections`` is not available.

    See Also
    --------
    jugeo.judgments.sections.JudgmentSection : The section type.
    jugeo.judgments.sections.SectionBuilder : Builder for sections.
    """
    try:
        from jugeo.judgments.sections import SectionBuilder
    except ImportError:
        raise NotImplementedError(
            "judgment_from_spec requires jugeo.judgments.sections to be installed."
        )

    results: list[dict[str, object]] = []
    constraints = getattr(spec, "constraints", None) or []
    for constraint in constraints:
        coord_id = str(getattr(constraint, "coordinate", "unknown"))
        error = None
        section = None
        try:
            builder = SectionBuilder()
            builder.set_coordinate(coord_id)
            judgment_str = getattr(constraint, "judgment", None)
            if judgment_str:
                builder.set_judgment(str(judgment_str))
            section = builder.build()
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        results.append({
            "coordinate": coord_id,
            "section": section,
            "error": error,
        })
    return results


def trust_graded_satisfaction(
    spec: "Specification",
    evidence_map: dict,
) -> "dict[str, object]":
    """Grade specification satisfaction by trust level.

    Uses the :mod:`jugeo.evidence.trust` algebra to compute per-coordinate
    and aggregate trust scores, partitioning the satisfaction result into
    trust tiers.

    Parameters
    ----------
    spec : Specification
        The specification being evaluated.
    evidence_map : dict
        Mapping from coordinate identifiers to evidence dictionaries.

    Returns
    -------
    dict[str, object]
        Keys: ``per_coordinate`` (dict mapping coordinate → trust level),
        ``aggregate_trust`` (the combined trust level), ``trust_algebra``
        (the :class:`~jugeo.evidence.trust.TrustAlgebra` instance used),
        ``tiers`` (dict grouping coordinates by tier name).

    Raises
    ------
    NotImplementedError
        If ``jugeo.evidence.trust`` is not available.

    See Also
    --------
    jugeo.evidence.trust.TrustAlgebra : The trust algebra.
    jugeo.evidence.trust.TrustLevel : Individual trust level type.
    """
    try:
        from jugeo.evidence.trust import TrustAlgebra, TrustLevel
    except ImportError:
        raise NotImplementedError(
            "trust_graded_satisfaction requires jugeo.evidence.trust to be installed."
        )

    algebra = TrustAlgebra()
    per_coordinate: dict[str, object] = {}
    tiers: dict[str, list[str]] = {}

    for coord_id, evidence in evidence_map.items():
        try:
            trust = algebra.assess(evidence)
        except Exception:  # noqa: BLE001
            trust = TrustLevel.minimum() if callable(getattr(TrustLevel, "minimum", None)) else None
        per_coordinate[coord_id] = trust
        tier_name = str(getattr(trust, "name", "UNKNOWN"))
        tiers.setdefault(tier_name, []).append(str(coord_id))

    try:
        aggregate = algebra.compose_all(list(per_coordinate.values()))
    except Exception:  # noqa: BLE001
        aggregate = None

    return {
        "per_coordinate": per_coordinate,
        "aggregate_trust": aggregate,
        "trust_algebra": algebra,
        "tiers": tiers,
    }


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    # -- models --
    "SpecificationKind",
    "WitnessStatus",
    "GapSeverity",
    "SatisfactionStatus",
    "DescentCondition",
    "Specification",
    "SatisfactionWitness",
    "CertificateOfSatisfaction",
    "ResidualGap",
    # -- s01 specifications --
    "SpecificationBuilder",
    "ConstraintEncoder",
    "SpecificationNormalizer",
    "SpecificationComposer",
    "GlobalSectionPrescription",
    "build_specification",
    "parse_constraint_list",
    "compose_specifications",
    "specification_from_template",
    "validate_specification",
    "TYPE_SAFE_TEMPLATE",
    "BEHAVIOR_CORRECT_TEMPLATE",
    "API_CONSISTENT_TEMPLATE",
    # -- s02 satisfaction witnesses --
    "WitnessBuilder",
    "EvidenceCollector",
    "GluingDataComputer",
    "WitnessMerger",
    "WitnessValidator",
    "build_witness",
    "collect_evidence_for_spec",
    "compute_gluing_data",
    "merge_witnesses",
    "validate_witness",
    # -- s03 descent conditions --
    "DescentConditionChecker",
    "OverlapCompatibilityVerifier",
    "GlobalSectionExtractor",
    "CocycleComputer",
    "DescentOrchestrator",
    "check_descent_conditions",
    "extract_global_section",
    "run_satisfaction_descent",
    "compute_cech_cocycle",
    "is_descent_possible",
    # -- s04 residual gaps --
    "GapAnalyzer",
    "ObstructionClassComputer",
    "RepairStrategyEngine",
    "GapPrioritizer",
    "GapTracker",
    "analyze_gaps",
    "compute_obstruction",
    "generate_repair_strategy",
    "prioritize_gaps",
    "track_gap_resolution",
    # -- algorithms --
    "SatisfactionAlgorithmResult",
    "IterationState",
    "TrustPropagator",
    "SpecificationCompositionAlgorithm",
    "ResidualMinimizer",
    "specification_satisfaction_algorithm",
    "descent_for_satisfaction",
    "gap_repair_algorithm",
    "iterative_satisfaction_loop",
    "trust_propagation_for_satisfaction",
    "specification_composition_algorithm",
    "residual_minimization_algorithm",
    # -- integration --
    "SpecificationSatisfactionIntegration",
    "SatisfactionExporter",
    "SatisfactionImporter",
    "SpecificationRegistry",
    "SolverConnector",
    "register_specification",
    "connect_to_solver",
    "build_integration",
    "export_result_to_json",
    "import_specification_from_json",
    # -- theorems --
    "VerificationStatus",
    "TheoremCategory",
    "Hypothesis",
    "TheoremConclusion",
    "ProofSketch",
    "TheoremStatement",
    "TheoremRegistry",
    "ProofVerifier",
    "theorem_satisfaction_iff_descent",
    "theorem_certificate_uniqueness",
    "theorem_gap_completeness",
    "theorem_monotone_satisfaction",
    "theorem_composition_satisfaction",
    "get_default_registry",
    "get_theorem",
    "verify_all_theorems",
    "list_theorem_ids",
    # -- manifest --
    "PackageManifest",
    "ModuleDescriptor",
    "get_manifest",
    "list_exports",
    "validate_package_integrity",
    "get_module_descriptor",
    "register_module",
    "PACKAGE_NAME",
    "VERSION",
    "AUTHOR",
    "CHAPTER",
    # -- convenience --
    "satisfy",
    "quick_check",
    "get_gaps",
    "build_spec",
    "run_descent",
    "compose",
    "get_theorems",
    "verify_spec",
    # -- cross-subsystem integration --
    "descent_verification",
    "judgment_from_spec",
    "trust_graded_satisfaction",
]


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import clausewise_truth
except Exception:
    pass
try:
    from . import descent_conditions
except Exception:
    pass
try:
    from . import generation_as_extension_partial_se
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import mixed_mode_programming_partial_sem
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import residual_gaps
except Exception:
    pass
try:
    from . import satisfaction_witnesses
except Exception:
    pass
try:
    from . import spec_parser
except Exception:
    pass
try:
    from . import specifications
except Exception:
    pass
try:
    from . import specifications_as_target_geometry
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
