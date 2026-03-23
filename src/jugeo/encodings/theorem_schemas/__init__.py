"""Package for Chapter 36 subsystem theorem schemas — copilot-assisted encoding.

This package encodes Chapter 36 of theory2.tex: *Subsystem Theorem Schemas*.
Chapter 36 specifies, for each of the eight JuGeo subsystems, the minimal set
of theorem schemas that the subsystem must prove before its outputs may be
consumed by downstream components.

copilot: package initialiser for theorem_schemas, encoding Ch36 of theory2.tex.

Package Structure
-----------------
The ``theorem_schemas`` package is organised into several modules:

``manifest``
    Module manifest metadata, schema registry, and ``SchemaDescriptor``
    objects.  Acts as the single source of truth for which schemas exist
    and which subsystem owns them.

``models``
    Core data model classes: ``TheoremSchema``, ``SubsystemSchema``,
    ``SchemaInstance``, ``ProofObligation``, ``SchemaValidator``, and the
    supporting enumerations ``ProofStyle``, ``InstanceStatus``,
    ``SubsystemKind``, ``ProofAgent``.

Public API
----------
Most consumers should import from this top-level package::

    from jugeo.encodings.theorem_schemas import (
        TheoremSchema, SubsystemKind, ProofStyle,
        build_complete_registry, get_package_info,
    )

For advanced usage, import directly from the sub-modules.

Versioning
----------
The package version is exposed via ``get_package_info()["version"]``.
The theory chapter reference is ``"Ch36"``.
"""
from __future__ import annotations

from jugeo.encodings.theorem_schemas.manifest import (
    CURRENT_VERSION,
    KNOWN_SUBSYSTEMS,
    SCHEMA_FORMAT_VERSION,
    SchemaDescriptor,
    SchemaRegistry,
    TheoremSchemasManifest,
    build_default_registry,
    build_manifest,
    validate_manifest,
)
from jugeo.encodings.theorem_schemas.models import (
    InstanceStatus,
    ProofAgent,
    ProofObligation,
    ProofStyle,
    SchemaInstance,
    SchemaValidator,
    SubsystemKind,
    SubsystemSchema,
    TheoremSchema,
    batch_instantiate,
    make_simple_schema,
    obligations_from_instances,
)

__all__ = [
    # --- manifest ---
    "CURRENT_VERSION",
    "KNOWN_SUBSYSTEMS",
    "SCHEMA_FORMAT_VERSION",
    "SchemaDescriptor",
    "SchemaRegistry",
    "TheoremSchemasManifest",
    "build_default_registry",
    "build_manifest",
    "validate_manifest",
    # --- models ---
    "InstanceStatus",
    "ProofAgent",
    "ProofObligation",
    "ProofStyle",
    "SchemaInstance",
    "SchemaValidator",
    "SubsystemKind",
    "SubsystemSchema",
    "TheoremSchema",
    "batch_instantiate",
    "make_simple_schema",
    "obligations_from_instances",
    # --- package helpers ---
    "get_package_info",
    "build_complete_registry",
    # --- cross-subsystem integration ---
    "schema_obligations_for_judgment",
    "descent_schema",
    "evaluation_schema",
]


def get_package_info() -> dict:
    """Return a dictionary of package metadata.

    This function provides a stable, introspectable description of the
    ``theorem_schemas`` package suitable for display in dashboards, logs, and
    documentation generators.

    Returns
    -------
    dict
        A dictionary with the following keys:

        ``name`` : str
            The fully-qualified package name.
        ``version`` : str
            Semantic version string (e.g. ``"1.0.0"``).
        ``chapter_ref`` : str
            Reference to the theory chapter (``"Ch36"``).
        ``description`` : str
            One-sentence description of the package.
        ``subsystems`` : list[str]
            List of the eight subsystems whose schemas are encoded.
        ``schema_format_version`` : str
            Serialisation format version.
        ``author`` : str
            Authoring team.

    Examples
    --------
    ::

        info = get_package_info()
        print(info["chapter_ref"])   # "Ch36"
        print(info["version"])       # "1.0.0"
    """
    return {
        "name": "jugeo.encodings.theorem_schemas",
        "version": CURRENT_VERSION,
        "chapter_ref": "Ch36",
        "description": (
            "Encodes Chapter 36 of theory2.tex: subsystem theorem schemas "
            "specifying what each JuGeo subsystem must prove."
        ),
        "subsystems": list(KNOWN_SUBSYSTEMS),
        "schema_format_version": SCHEMA_FORMAT_VERSION,
        "author": "jugeo",
    }


def build_complete_registry() -> SchemaRegistry:
    """Build and return a ``SchemaRegistry`` pre-populated with all default schemas.

    This is the recommended entry-point for tools that need a ready-to-use
    registry of all known theorem schema descriptors.  It delegates to
    ``build_default_registry()`` from the ``manifest`` module, which creates
    one ``SchemaDescriptor`` per known subsystem.

    Returns
    -------
    SchemaRegistry
        A registry containing one descriptor per subsystem in
        ``KNOWN_SUBSYSTEMS``.

    Examples
    --------
    ::

        registry = build_complete_registry()
        assert registry.count() == len(KNOWN_SUBSYSTEMS)
        descs = registry.list_by_subsystem("TRUST")
    """
    return build_default_registry()


# ---------------------------------------------------------------------------
# Convenience schema factories for each subsystem
# ---------------------------------------------------------------------------


def make_descent_schema(
    name: str,
    statement: str,
    variables: dict[str, str],
) -> TheoremSchema:
    """Create a DESCENT subsystem theorem schema.

    Factory helper that fixes ``subsystem=SubsystemKind.DESCENT`` and
    ``proof_style=ProofStyle.CATEGORICAL``, reflecting the typical proof
    strategy for descent-data coherence theorems.

    Parameters
    ----------
    name:
        Short stable identifier for the schema.
    statement:
        Template statement with ``{var}`` placeholders.
    variables:
        Mapping from placeholder name to mathematical description.

    Returns
    -------
    TheoremSchema
        A schema owned by the DESCENT subsystem.

    Examples
    --------
    ::

        schema = make_descent_schema(
            "descent-coherence",
            "cohDatum({X}, {Y}, {f}) holds for all morphisms {f}: {X} -> {Y}",
            {"X": "source object", "Y": "target object", "f": "morphism"},
        )
    """
    return TheoremSchema(
        name=name,
        template_statement=statement,
        variables=variables,
        proof_style=ProofStyle.CATEGORICAL,
        subsystem=SubsystemKind.DESCENT,
    )


def make_trust_schema(
    name: str,
    statement: str,
    variables: dict[str, str],
) -> TheoremSchema:
    """Create a TRUST subsystem theorem schema.

    Factory helper that fixes ``subsystem=SubsystemKind.TRUST`` and
    ``proof_style=ProofStyle.INDUCTIVE``, reflecting the inductive
    structure of trust-propagation monotonicity arguments.

    Parameters
    ----------
    name:
        Short stable identifier for the schema.
    statement:
        Template statement with ``{var}`` placeholders.
    variables:
        Mapping from placeholder name to mathematical description.

    Returns
    -------
    TheoremSchema
        A schema owned by the TRUST subsystem.
    """
    return TheoremSchema(
        name=name,
        template_statement=statement,
        variables=variables,
        proof_style=ProofStyle.INDUCTIVE,
        subsystem=SubsystemKind.TRUST,
    )


def make_evidence_schema(
    name: str,
    statement: str,
    variables: dict[str, str],
) -> TheoremSchema:
    """Create an EVIDENCE subsystem theorem schema.

    Parameters
    ----------
    name:
        Short stable identifier.
    statement:
        Template statement with ``{var}`` placeholders.
    variables:
        Variable descriptions.

    Returns
    -------
    TheoremSchema
        A schema owned by the EVIDENCE subsystem.
    """
    return TheoremSchema(
        name=name,
        template_statement=statement,
        variables=variables,
        proof_style=ProofStyle.DIRECT,
        subsystem=SubsystemKind.EVIDENCE,
    )


def make_federation_schema(
    name: str,
    statement: str,
    variables: dict[str, str],
) -> TheoremSchema:
    """Create a FEDERATION subsystem theorem schema.

    Parameters
    ----------
    name:
        Short stable identifier.
    statement:
        Template statement with ``{var}`` placeholders.
    variables:
        Variable descriptions.

    Returns
    -------
    TheoremSchema
        A schema owned by the FEDERATION subsystem.
    """
    return TheoremSchema(
        name=name,
        template_statement=statement,
        variables=variables,
        proof_style=ProofStyle.CONTRADICTION,
        subsystem=SubsystemKind.FEDERATION,
    )


def make_invalidation_schema(
    name: str,
    statement: str,
    variables: dict[str, str],
) -> TheoremSchema:
    """Create an INVALIDATION subsystem theorem schema.

    Parameters
    ----------
    name:
        Short stable identifier.
    statement:
        Template statement with ``{var}`` placeholders.
    variables:
        Variable descriptions.

    Returns
    -------
    TheoremSchema
        A schema owned by the INVALIDATION subsystem.
    """
    return TheoremSchema(
        name=name,
        template_statement=statement,
        variables=variables,
        proof_style=ProofStyle.INDUCTIVE,
        subsystem=SubsystemKind.INVALIDATION,
    )


def make_memory_schema(
    name: str,
    statement: str,
    variables: dict[str, str],
) -> TheoremSchema:
    """Create a MEMORY subsystem theorem schema.

    Parameters
    ----------
    name:
        Short stable identifier.
    statement:
        Template statement with ``{var}`` placeholders.
    variables:
        Variable descriptions.

    Returns
    -------
    TheoremSchema
        A schema owned by the MEMORY subsystem.
    """
    return TheoremSchema(
        name=name,
        template_statement=statement,
        variables=variables,
        proof_style=ProofStyle.DIRECT,
        subsystem=SubsystemKind.MEMORY,
    )


def make_judgment_schema(
    name: str,
    statement: str,
    variables: dict[str, str],
) -> TheoremSchema:
    """Create a JUDGMENT subsystem theorem schema.

    Parameters
    ----------
    name:
        Short stable identifier.
    statement:
        Template statement with ``{var}`` placeholders.
    variables:
        Variable descriptions.

    Returns
    -------
    TheoremSchema
        A schema owned by the JUDGMENT subsystem.
    """
    return TheoremSchema(
        name=name,
        template_statement=statement,
        variables=variables,
        proof_style=ProofStyle.CATEGORICAL,
        subsystem=SubsystemKind.JUDGMENT,
    )


def make_encoding_schema(
    name: str,
    statement: str,
    variables: dict[str, str],
) -> TheoremSchema:
    """Create an ENCODING subsystem theorem schema.

    Parameters
    ----------
    name:
        Short stable identifier.
    statement:
        Template statement with ``{var}`` placeholders.
    variables:
        Variable descriptions.

    Returns
    -------
    TheoremSchema
        A schema owned by the ENCODING subsystem.
    """
    return TheoremSchema(
        name=name,
        template_statement=statement,
        variables=variables,
        proof_style=ProofStyle.DIRECT,
        subsystem=SubsystemKind.ENCODING,
    )


# ---------------------------------------------------------------------------
# Built-in sample schemas for each subsystem
# ---------------------------------------------------------------------------


def get_sample_schemas() -> dict[str, list[TheoremSchema]]:
    """Return a dictionary mapping each subsystem name to a list of sample schemas.

    These sample schemas are derived directly from Chapter 36 of theory2.tex
    and represent the canonical proof obligations for each subsystem.  They
    can be used for testing, documentation, and as a starting point for
    custom proof developments.

    Returns
    -------
    dict[str, list[TheoremSchema]]
        Mapping from subsystem name string to list of sample schemas.

    Examples
    --------
    ::

        samples = get_sample_schemas()
        for sub, schemas in samples.items():
            for s in schemas:
                print(s.summarize())
    """
    return {
        "DESCENT": [
            make_descent_schema(
                "descent-datum-coherence",
                "cohDatum({X}, {Y}, {f}) is functorial in {f}",
                {"X": "source object", "Y": "target object", "f": "morphism"},
            ),
            make_descent_schema(
                "descent-gluing",
                "glue({cover}, {sections}) yields a unique global section over {base}",
                {"cover": "cover sieve", "sections": "local sections", "base": "base object"},
            ),
        ],
        "TRUST": [
            make_trust_schema(
                "trust-monotone",
                "trust({A}) <= trust(propagate({A}, {S}))",
                {"A": "trust annotation", "S": "support set"},
            ),
            make_trust_schema(
                "trust-composition",
                "trust(compose({A}, {B})) >= min(trust({A}), trust({B}))",
                {"A": "first annotation", "B": "second annotation"},
            ),
        ],
        "EVIDENCE": [
            make_evidence_schema(
                "evidence-soundness",
                "accept({E}, {H}) -> consistent({E}, {H})",
                {"E": "evidence bundle", "H": "hypothesis"},
            ),
            make_evidence_schema(
                "evidence-completeness",
                "consistent({E}, {H}) -> exists {E2}: accept({E2}, {H})",
                {"E": "evidence bundle", "H": "hypothesis", "E2": "extended bundle"},
            ),
        ],
        "FEDERATION": [
            make_federation_schema(
                "federation-agreement",
                "agree({N1}, {N2}, {msg}) -> not disagree({N1}, {N2}, {msg})",
                {"N1": "first node", "N2": "second node", "msg": "message"},
            ),
        ],
        "INVALIDATION": [
            make_invalidation_schema(
                "invalidation-termination",
                "cascade({G}, {n}) terminates in finite steps for finite {G}",
                {"G": "invalidation graph", "n": "starting node"},
            ),
        ],
        "MEMORY": [
            make_memory_schema(
                "memory-snapshot-consistency",
                "snapshot({M}, {t}) reflects state({M}) at time {t}",
                {"M": "memory store", "t": "timestamp"},
            ),
        ],
        "JUDGMENT": [
            make_judgment_schema(
                "judgment-associativity",
                "compose({J1}, compose({J2}, {J3})) = compose(compose({J1}, {J2}), {J3})",
                {"J1": "first judgment", "J2": "second judgment", "J3": "third judgment"},
            ),
        ],
        "ENCODING": [
            make_encoding_schema(
                "encoding-roundtrip",
                "decode(encode({obj}, {codec}), {codec}) = {obj}",
                {"obj": "encodable object", "codec": "codec identifier"},
            ),
        ],
    }


def build_subsystem_registry() -> dict[str, SubsystemSchema]:
    """Build a dictionary of SubsystemSchema objects, one per known subsystem.

    Each SubsystemSchema is populated with the sample schemas returned by
    ``get_sample_schemas()`` and has its ``required_theorems`` set to the
    names of those schemas.

    Returns
    -------
    dict[str, SubsystemSchema]
        Mapping from subsystem name string to populated ``SubsystemSchema``.

    Examples
    --------
    ::

        reg = build_subsystem_registry()
        trust_sub = reg["TRUST"]
        assert trust_sub.validate_completeness()
    """
    sample_map = get_sample_schemas()
    registry: dict[str, SubsystemSchema] = {}
    kind_map = {k.value.upper(): k for k in SubsystemKind}
    for sub_name in KNOWN_SUBSYSTEMS:
        kind_key = sub_name.upper()
        kind = kind_map.get(kind_key, SubsystemKind.ENCODING)
        schemas = sample_map.get(sub_name, [])
        required_names = [s.name for s in schemas]
        sub_schema = SubsystemSchema(
            subsystem_name=kind,
            required_theorems=required_names,
            optional_theorems=[],
        )
        for schema in schemas:
            sub_schema.add_schema(schema)
        registry[sub_name] = sub_schema
    return registry


def check_all_subsystems_complete() -> dict[str, bool]:
    """Check completeness of all subsystem schemas built by ``build_subsystem_registry``.

    Returns
    -------
    dict[str, bool]
        Mapping from subsystem name to a boolean indicating whether all
        required theorems have a registered schema.

    Examples
    --------
    ::

        status = check_all_subsystems_complete()
        incomplete = [k for k, v in status.items() if not v]
        if incomplete:
            print(f"Incomplete subsystems: {incomplete}")
    """
    reg = build_subsystem_registry()
    return {name: sub.validate_completeness() for name, sub in reg.items()}


def summarize_package() -> str:
    """Return a comprehensive multi-line summary of the package contents.

    Returns
    -------
    str
        A formatted string describing the package version, subsystems,
        sample schema counts, and completeness status.
    """
    info = get_package_info()
    completeness = check_all_subsystems_complete()
    sample_map = get_sample_schemas()
    lines = [
        f"theorem_schemas package v{info['version']} ({info['chapter_ref']})",
        f"  Author: {info['author']}",
        f"  Description: {info['description']}",
        f"  Subsystems ({len(info['subsystems'])}):",
    ]
    for sub in info["subsystems"]:
        count = len(sample_map.get(sub, []))
        complete = completeness.get(sub, False)
        status = "complete" if complete else "incomplete"
        lines.append(f"    {sub}: {count} sample schema(s) [{status}]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_all_sample_schemas() -> dict[str, list[str]]:
    """Run ``SchemaValidator`` over all sample schemas and return errors.

    Returns
    -------
    dict[str, list[str]]
        Mapping from schema_id to list of validation error strings.
        An empty list for a given schema means it passed all checks.

    Examples
    --------
    ::

        errors = validate_all_sample_schemas()
        failures = {k: v for k, v in errors.items() if v}
        assert not failures, f"Schemas with errors: {failures}"
    """
    validator = SchemaValidator(strict_mode=False)
    all_schemas: list[TheoremSchema] = []
    for schemas in get_sample_schemas().values():
        all_schemas.extend(schemas)
    return validator.run_all_checks(all_schemas)


# ---------------------------------------------------------------------------
# Cross-subsystem integration — judgments, descent, evaluation
# ---------------------------------------------------------------------------

from typing import Any

try:
    from jugeo.judgments.judgment_terms import Judgment  # type: ignore[import]
except ImportError:
    Judgment = None  # type: ignore[assignment]

try:
    from jugeo.judgments.sections import Section  # type: ignore[import]
except ImportError:
    Section = None  # type: ignore[assignment]

try:
    from jugeo.geometry.descent import DescentEngine, DescentConfiguration  # type: ignore[import]
except ImportError:
    DescentEngine = None  # type: ignore[assignment]
    DescentConfiguration = None  # type: ignore[assignment]

try:
    from jugeo.evaluation.evaluation_design import EvaluationDesign, ClausewiseEvaluator  # type: ignore[import]
except ImportError:
    EvaluationDesign = None  # type: ignore[assignment]
    ClausewiseEvaluator = None  # type: ignore[assignment]


def schema_obligations_for_judgment(judgment: object) -> list["ProofObligation"]:
    """Generate proof obligations for a judgment from the judgments subsystem.

    Inspects the judgment's structure (coordinate kind, evidence bundle,
    trust annotation, obstructions) to determine which theorem schemas
    apply, then instantiates those schemas to produce a list of concrete
    ``ProofObligation`` objects.

    Parameters
    ----------
    judgment:
        A ``jugeo.judgments.judgment_terms.Judgment`` instance.

    Returns
    -------
    list[ProofObligation]
        Proof obligations that must be discharged for the given judgment.

    Examples
    --------
    ::

        from jugeo.judgments.judgment_terms import JudgmentBuilder
        j = JudgmentBuilder().build()
        obligations = schema_obligations_for_judgment(j)
        for ob in obligations:
            print(ob.summarize())
    """
    obligations: list[ProofObligation] = []
    registry = build_subsystem_registry()
    samples = get_sample_schemas()

    # Determine which subsystems the judgment touches
    relevant_subsystems: list[str] = []

    trust = getattr(judgment, "trust_annotation", None)
    if trust is not None:
        relevant_subsystems.append("TRUST")

    evidence = getattr(judgment, "evidence_bundle", None)
    if evidence is not None:
        relevant_subsystems.append("EVIDENCE")

    obstructions = getattr(judgment, "obstructions", None)
    if obstructions:
        relevant_subsystems.append("INVALIDATION")

    coord = getattr(judgment, "coordinate", None)
    if coord is not None:
        kind = getattr(coord, "kind", None)
        kind_val = kind.value if hasattr(kind, "value") else str(kind) if kind else ""
        if "DESCENT" in kind_val.upper():
            relevant_subsystems.append("DESCENT")

    # Always include JUDGMENT subsystem
    relevant_subsystems.append("JUDGMENT")

    for sub_name in relevant_subsystems:
        schemas = samples.get(sub_name, [])
        for schema in schemas:
            try:
                instances = schema.instantiate(judgment=judgment)
                new_obs = obligations_from_instances(instances)
                obligations.extend(new_obs)
            except Exception:
                ob = ProofObligation(
                    schema_name=schema.name,
                    subsystem=sub_name,
                    judgment_id=getattr(judgment, "judgment_id", None),
                    status="pending",
                )
                obligations.append(ob)

    return obligations


def descent_schema(
    name: str = "descent-condition",
    descent_config: object | None = None,
) -> "TheoremSchema":
    """Create a theorem schema encoding descent conditions.

    Consults ``jugeo.geometry.descent.DescentEngine`` (when available) to
    extract the overlap-compatibility and gluing conditions, then wraps
    them as a ``TheoremSchema`` of the DESCENT subsystem.

    Parameters
    ----------
    name:
        Short stable identifier for the schema.
    descent_config:
        Optional ``jugeo.geometry.descent.DescentConfiguration``.  When
        provided its parameters are incorporated into the schema variables.

    Returns
    -------
    TheoremSchema
        A DESCENT theorem schema capturing the descent condition.

    Examples
    --------
    ::

        schema = descent_schema("my-descent")
        print(schema.summarize())
    """
    variables: dict[str, str] = {
        "cover": "covering family from the Grothendieck topology",
        "sections": "local sections over the cover",
        "overlaps": "overlap compatibility data",
    }
    statement = (
        "glue({cover}, {sections}) succeeds iff overlap_compatible({overlaps}) "
        "holds for all pairwise overlaps in {cover}"
    )

    if descent_config is not None:
        strategy = getattr(descent_config, "strategy", None)
        if strategy is not None:
            variables["strategy"] = str(strategy)
            statement += " under strategy {strategy}"

    # Enrich with descent engine metadata when available
    if DescentEngine is not None:
        try:
            engine = DescentEngine()
            overlap_keys = engine.overlap_condition_keys()
            variables["conditions"] = ", ".join(overlap_keys) if overlap_keys else "standard"
        except Exception:
            pass

    return make_descent_schema(name=name, statement=statement, variables=variables)


def evaluation_schema(
    name: str = "evaluation-criterion",
    design: object | None = None,
) -> "TheoremSchema":
    """Create a theorem schema for evaluation criteria.

    When a ``jugeo.evaluation.evaluation_design.EvaluationDesign`` is
    provided, its clause structure is used to generate the schema template.
    Otherwise a generic evaluation-soundness schema is returned.

    Parameters
    ----------
    name:
        Short stable identifier for the schema.
    design:
        Optional ``EvaluationDesign`` from the evaluation subsystem.

    Returns
    -------
    TheoremSchema
        An ENCODING theorem schema encoding the evaluation criterion.

    Examples
    --------
    ::

        schema = evaluation_schema("eval-soundness")
        print(schema.summarize())
    """
    variables: dict[str, str] = {
        "criterion": "evaluation criterion identifier",
        "result": "evaluation result",
        "evidence": "supporting evidence bundle",
    }
    statement = (
        "evaluate({criterion}, {evidence}) = {result} implies "
        "soundness({criterion}, {result}) holds"
    )

    if design is not None:
        clauses = getattr(design, "clauses", None)
        if clauses:
            clause_names = [
                getattr(c, "name", str(c)) for c in (clauses[:5] if len(clauses) > 5 else clauses)
            ]
            variables["clauses"] = ", ".join(clause_names)
            statement += " for clauses {clauses}"

    # Enrich with evaluator metadata when available
    if ClausewiseEvaluator is not None:
        try:
            evaluator = ClausewiseEvaluator()
            variables["evaluator_version"] = getattr(evaluator, "version", "1.0")
        except Exception:
            pass

    return make_encoding_schema(name=name, statement=statement, variables=variables)


# --- auto-registered submodules ---
try:
    from . import algorithms
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
    from . import models
except Exception:
    pass
try:
    from . import obligation_discharge
except Exception:
    pass
try:
    from . import proof_obligations
except Exception:
    pass
try:
    from . import schema_templates
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
