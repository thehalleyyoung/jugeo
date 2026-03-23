"""Manifest for the repair_semantics subsystem (theory2.tex Ch11).

The repair-semantics subsystem implements the Ch11 debugging pipeline:
counterexample extraction, repair planning, repair execution, and debug
orchestration.  This manifest declares the subsystem's capabilities,
theorem targets, and provenance metadata so that the JuGeo orchestration
layer can route, validate, and report on repair operations.

Theory basis
------------
From theory2.tex Ch11 — Debugging, Counterexamples, and Repair:

* Counterexamples are first-class semantic objects — cohomology classes in
  Ȟ¹(𝔘, 𝒟) — not disposable error strings.
* Repair is local-section replacement followed by a descent re-check.
* The repair frontier is the minimal set of coordinates requiring modification.
* A repair is admissible iff the patched sections pass all descent conditions.

Cohomological framing
---------------------
The central insight of Ch11 is that a failed judgment ψ at coordinate c
produces a Čech 1-cochain

    δ(ψ)(U_i ∩ U_j)  ∈  𝒟(U_i ∩ U_j)

whose class [δ(ψ)] ∈ Ȟ¹(𝔘, 𝒟) is the canonical obstruction.  This class
is:
  - trivial  ⟺  the failure is locally repairable (a coboundary)
  - non-trivial ⟺  a global structural repair is needed

The repair pipeline must therefore:
  1. Extract and classify [δ(ψ)].
  2. Compute the repair frontier — the minimal cover of coordinate c.
  3. Replace local sections on the frontier patches.
  4. Re-run descent conditions to certify admissibility.

Subsystem responsibilities
--------------------------
The repair_semantics subsystem owns the following pipeline stages:

  COUNTEREXAMPLE_EXTRACTION  — convert solver output to ObstructionRecord
  REPAIR_PLANNING            — enumerate repair steps and dependencies
  REPAIR_EXECUTION           — apply section replacements
  DEBUG_ORCHESTRATION        — drive the iterative debug loop
  COHOMOLOGY_CLASSIFICATION  — classify the cohomology class
  FRONTIER_MINIMIZATION      — prune the repair frontier
  DESCENT_VALIDATION         — certify the repaired sheaf
  SESSION_TRACKING           — persist iteration history
  CONVERGENCE_CHECKING       — detect fixpoint / divergence
  REPAIR_CERTIFICATE_EMISSION — emit the final proof certificate

# copilot: repair_semantics manifest — theory2 ch11 provenance
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Module-level provenance
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-repair-semantics",
    "sequence": "11",
    "semantic_source": "preliminaries/theory2.tex",
    "chapter": "Ch11 — Debugging, Counterexamples, and Repair",
    "subsystem": "repair_semantics",
    "generated_by": "jugeo.problem_modes.repair_semantics.manifest",
    "theory_version": "2.0",
    "repair_model": "local-section-replacement-with-descent-check",
    "counterexample_model": "cohomology-class-H1",
    "frontier_model": "minimal-coordinate-set",
}

# ---------------------------------------------------------------------------
# Theorem targets from Ch11
# ---------------------------------------------------------------------------

THEOREM_TARGETS: tuple[str, ...] = (
    # Thm 11.1 — every minimal counterexample is a generating set for the
    # corresponding obstruction class.
    "counterexample_minimality",
    # Thm 11.2 — a repair is admissible iff it eliminates the obstruction
    # class and passes all pairwise descent conditions.
    "repair_admissibility",
    # Thm 11.3 — admissible repairs preserve the descent morphisms on the
    # un-modified coordinate neighbourhood.
    "descent_preservation",
    # Thm 11.4 — the repair frontier is unique up to refinement of the
    # covering 𝔘.
    "frontier_minimality",
    # Thm 11.5 — the iterative repair loop converges in at most |coord| steps
    # for finite-dimensional coordinate spaces.
    "repair_convergence",
    # Thm 11.6 — cohomology classes extracted from distinct counterexamples
    # at the same coordinate are consistently classified.
    "cohomology_class_consistency",
    # Thm 11.7 — the debug session accumulates counterexamples monotonically;
    # no previously witnessed obstruction is retracted.
    "session_monotonicity",
    # Thm 11.8 — local-section replacement is sound: the replaced section
    # satisfies the local judgment at every patch in the frontier.
    "local_section_replacement_soundness",
)


# ---------------------------------------------------------------------------
# Capability enum
# ---------------------------------------------------------------------------

class RepairSemanticsCap(str, Enum):
    """Enumeration of atomic capabilities exported by the repair_semantics subsystem.

    Each value is a stable string identifier used in routing tables, telemetry,
    and capability negotiation between the orchestration layer and this
    subsystem.

    Theory reference: Ch11 §11.1 — Subsystem Capability Model.

    Design rationale
    ----------------
    String-valued enums allow capabilities to be serialised to JSON without a
    custom encoder.  The subsystem declares exactly the capabilities it
    implements; the orchestration layer rejects requests that name a
    capability absent from this set.
    """

    COUNTEREXAMPLE_EXTRACTION = "counterexample_extraction"
    """Convert raw solver output into a first-class ObstructionRecord."""

    REPAIR_PLANNING = "repair_planning"
    """Enumerate repair steps and their dependency ordering."""

    REPAIR_EXECUTION = "repair_execution"
    """Apply section replacements identified by a RepairPlan."""

    DEBUG_ORCHESTRATION = "debug_orchestration"
    """Drive the iterative counterexample-repair loop to convergence."""

    COHOMOLOGY_CLASSIFICATION = "cohomology_classification"
    """Classify the obstruction as a Čech cohomology class in Ȟ¹."""

    FRONTIER_MINIMIZATION = "frontier_minimization"
    """Prune the repair frontier to its minimal covering set."""

    DESCENT_VALIDATION = "descent_validation"
    """Certify that the patched sheaf satisfies all descent conditions."""

    SESSION_TRACKING = "session_tracking"
    """Persist and retrieve debug session iteration history."""

    CONVERGENCE_CHECKING = "convergence_checking"
    """Detect fixpoint or divergence in the repair loop."""

    REPAIR_CERTIFICATE_EMISSION = "repair_certificate_emission"
    """Emit a machine-verifiable certificate for a completed repair."""


# ---------------------------------------------------------------------------
# RepairSemanticsCapability dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RepairSemanticsCapability:
    """Describes one atomic capability of the repair_semantics subsystem.

    Each capability is described by:
    - its enum identity (``cap``),
    - a human-readable description,
    - the theorem(s) from Ch11 that justify it (``theory_reference``),
    - whether it is required for a minimal repair pipeline
      (``is_required``), and
    - the set of other capabilities it depends on (``depends_on``).

    Theory basis
    ------------
    Ch11 §11.1 defines a capability lattice where the partial order
    represents logical dependency: capability A ≤ B if B's implementation
    requires A.  The ``depends_on`` field encodes this partial order.

    A repair pipeline is *minimal* if it contains exactly the required
    capabilities and their transitive dependencies.

    JSON round-trip
    ---------------
    ``to_dict`` / ``from_dict`` provide lossless serialisation suitable for
    manifest files, telemetry payloads, and orchestration-layer capability
    negotiation.

    Parameters
    ----------
    cap:
        The ``RepairSemanticsCap`` value identifying this capability.
    description:
        A short (≤ 200 char) human-readable description.
    theory_reference:
        A citation of the form "Ch11 §X.Y — <theorem name>".
    is_required:
        Whether this capability must be present in every repair pipeline.
    depends_on:
        Tuple of ``RepairSemanticsCap`` values that must be present before
        this capability can be exercised.

    Examples
    --------
    >>> cap = RepairSemanticsCapability(
    ...     cap=RepairSemanticsCap.COUNTEREXAMPLE_EXTRACTION,
    ...     description="Extract counterexamples from solver output.",
    ...     theory_reference="Ch11 §11.2 — counterexample_minimality",
    ...     is_required=True,
    ...     depends_on=(),
    ... )
    >>> cap.to_dict()["cap"] == "counterexample_extraction"
    True
    """

    cap: RepairSemanticsCap
    description: str
    theory_reference: str
    is_required: bool
    depends_on: tuple[RepairSemanticsCap, ...]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this capability to a plain-Python dict.

        Returns
        -------
        dict[str, Any]
            A JSON-serialisable dict with keys ``cap``, ``description``,
            ``theory_reference``, ``is_required``, and ``depends_on``.

        Notes
        -----
        The ``cap`` and each element of ``depends_on`` are serialised as
        their string values so that the output can be round-tripped through
        ``from_dict`` without importing the enum.
        """
        return {
            "cap": self.cap.value,
            "description": self.description,
            "theory_reference": self.theory_reference,
            "is_required": self.is_required,
            "depends_on": [c.value for c in self.depends_on],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RepairSemanticsCapability:
        """Deserialise a capability from a plain-Python dict.

        Parameters
        ----------
        payload:
            A dict as produced by ``to_dict``.

        Returns
        -------
        RepairSemanticsCapability
            The reconstructed capability.

        Raises
        ------
        KeyError
            If a required key is missing from ``payload``.
        ValueError
            If a string value cannot be mapped to a ``RepairSemanticsCap``.
        """
        return cls(
            cap=RepairSemanticsCap(payload["cap"]),
            description=payload["description"],
            theory_reference=payload["theory_reference"],
            is_required=payload["is_required"],
            depends_on=tuple(RepairSemanticsCap(c) for c in payload.get("depends_on", [])),
        )


# ---------------------------------------------------------------------------
# RepairSemanticsManifest dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RepairSemanticsManifest:
    """Top-level manifest for the repair_semantics subsystem.

    The manifest is the authoritative source of truth for what the
    repair_semantics subsystem *can* do, *should* do, and *has done* in
    terms of theoretical coverage.  It is consumed by:

    * The orchestration layer, to route requests and validate pipeline
      configurations.
    * The telemetry layer, to annotate traces with provenance metadata.
    * The test harness, to enumerate theorem targets for property-based
      testing.

    Theoretical foundation
    ----------------------
    From theory2.tex Ch11 §11.1:

      A subsystem manifest **M** is a triple (P, T, C) where

        P = provenance metadata (``spec_provenance``)
        T = theorem targets (``theorem_targets``)
        C = capability set (``capabilities``)

      **M** is *valid* iff:
        1. Every required capability in C is present.
        2. Every capability's ``depends_on`` set is a subset of C.
        3. The capability dependency graph is acyclic.
        4. Every theorem in T is reachable from at least one capability's
           ``theory_reference``.

    Repair model
    ------------
    The ``repair_model`` field names the algebraic model used for section
    replacement.  The default value is
    ``"local-section-replacement-with-descent-check"``, corresponding to
    Def. 11.7 in theory2.tex.

    Counterexample model
    --------------------
    The ``counterexample_model`` field names the cohomological model for
    counterexamples.  The default value is ``"cohomology-class-H1"``,
    corresponding to the identification of counterexamples with Čech
    1-cohomology classes established in Thm. 11.1.

    Parameters
    ----------
    spec_provenance:
        Provenance metadata dict (see ``MANIFEST_SPEC_PROVENANCE``).
    theorem_targets:
        Tuple of theorem names from Ch11 that this subsystem targets.
    capabilities:
        Tuple of ``RepairSemanticsCapability`` objects.
    subsystem_name:
        Short identifier for this subsystem.
    version:
        Semantic version string.
    description:
        Human-readable description of the subsystem.
    theory_chapter:
        The theory chapter this subsystem corresponds to.
    repair_model:
        Name of the algebraic repair model.
    counterexample_model:
        Name of the cohomological counterexample model.

    Examples
    --------
    >>> manifest = get_manifest()
    >>> manifest.has_capability(RepairSemanticsCap.COUNTEREXAMPLE_EXTRACTION)
    True
    >>> len(manifest.theorem_targets) >= 8
    True
    """

    spec_provenance: dict[str, str]
    theorem_targets: tuple[str, ...]
    capabilities: tuple[RepairSemanticsCapability, ...]
    subsystem_name: str
    version: str
    description: str
    theory_chapter: str
    repair_model: str
    counterexample_model: str

    # ------------------------------------------------------------------
    # Capability queries
    # ------------------------------------------------------------------

    def capability_names(self) -> frozenset[str]:
        """Return the frozenset of all capability string values.

        Returns
        -------
        frozenset[str]
            The ``cap.value`` for every capability in this manifest.
        """
        return frozenset(c.cap.value for c in self.capabilities)

    def has_capability(self, cap: RepairSemanticsCap) -> bool:
        """Test whether a given capability is declared in this manifest.

        Parameters
        ----------
        cap:
            The ``RepairSemanticsCap`` to look up.

        Returns
        -------
        bool
            ``True`` iff ``cap`` is present in ``self.capabilities``.
        """
        return any(c.cap == cap for c in self.capabilities)

    def get_capability(self, cap: RepairSemanticsCap) -> RepairSemanticsCapability | None:
        """Look up a capability by its enum value.

        Parameters
        ----------
        cap:
            The ``RepairSemanticsCap`` to look up.

        Returns
        -------
        RepairSemanticsCapability | None
            The matching capability, or ``None`` if not found.
        """
        for c in self.capabilities:
            if c.cap == cap:
                return c
        return None

    def required_capabilities(self) -> tuple[RepairSemanticsCapability, ...]:
        """Return only the capabilities marked ``is_required=True``.

        Returns
        -------
        tuple[RepairSemanticsCapability, ...]
            A tuple of required capabilities, in declaration order.
        """
        return tuple(c for c in self.capabilities if c.is_required)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate the manifest and return a list of error strings.

        Checks performed:

        1. All required capabilities are present.
        2. Every capability's ``depends_on`` references a cap in the manifest.
        3. The capability dependency graph is acyclic.
        4. At least one theorem target exists.

        Returns
        -------
        list[str]
            An empty list if the manifest is valid; otherwise a list of
            human-readable error descriptions.
        """
        errors: list[str] = []
        declared = {c.cap for c in self.capabilities}

        for c in self.capabilities:
            for dep in c.depends_on:
                if dep not in declared:
                    errors.append(
                        f"Capability {c.cap.value!r} depends on undeclared"
                        f" capability {dep.value!r}."
                    )

        errors.extend(_validate_capability_graph(self.capabilities))

        if not self.theorem_targets:
            errors.append("Manifest declares no theorem targets.")

        return errors

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this manifest to a plain-Python dict.

        Returns
        -------
        dict[str, Any]
            A JSON-serialisable representation of the manifest including all
            capabilities and provenance metadata.
        """
        return {
            "spec_provenance": dict(self.spec_provenance),
            "theorem_targets": list(self.theorem_targets),
            "capabilities": [c.to_dict() for c in self.capabilities],
            "subsystem_name": self.subsystem_name,
            "version": self.version,
            "description": self.description,
            "theory_chapter": self.theory_chapter,
            "repair_model": self.repair_model,
            "counterexample_model": self.counterexample_model,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RepairSemanticsManifest:
        """Deserialise a manifest from a plain-Python dict.

        Parameters
        ----------
        payload:
            A dict as produced by ``to_dict``.

        Returns
        -------
        RepairSemanticsManifest
            The reconstructed manifest.

        Raises
        ------
        KeyError
            If a required key is missing from ``payload``.
        """
        return cls(
            spec_provenance=dict(payload["spec_provenance"]),
            theorem_targets=tuple(payload["theorem_targets"]),
            capabilities=tuple(
                RepairSemanticsCapability.from_dict(c)
                for c in payload["capabilities"]
            ),
            subsystem_name=payload["subsystem_name"],
            version=payload["version"],
            description=payload["description"],
            theory_chapter=payload["theory_chapter"],
            repair_model=payload["repair_model"],
            counterexample_model=payload["counterexample_model"],
        )

    def summary(self) -> str:
        """Return a short human-readable summary of the manifest.

        Returns
        -------
        str
            A multi-line summary including subsystem name, version, theorem
            count, and capability count.
        """
        req = len(self.required_capabilities())
        total = len(self.capabilities)
        thms = len(self.theorem_targets)
        lines = [
            f"Manifest: {self.subsystem_name} v{self.version}",
            f"  Chapter : {self.theory_chapter}",
            f"  Theorems: {thms}",
            f"  Capabilities: {total} total, {req} required",
            f"  Repair model: {self.repair_model}",
            f"  Counterexample model: {self.counterexample_model}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_capabilities() -> tuple[RepairSemanticsCapability, ...]:
    """Construct the full capability tuple for the repair_semantics subsystem.

    This factory function creates all ten ``RepairSemanticsCapability``
    instances with their dependency edges and theory references.  It is
    called once at module load time to populate the module-level
    ``_CAPABILITY_REGISTRY`` and the ``REPAIR_SEMANTICS_MANIFEST`` singleton.

    Returns
    -------
    tuple[RepairSemanticsCapability, ...]
        An ordered tuple of all declared capabilities.

    Notes
    -----
    The ordering follows the topological order of the capability dependency
    graph: capabilities with no dependencies come first, dependent
    capabilities come later.
    """
    return (
        RepairSemanticsCapability(
            cap=RepairSemanticsCap.COUNTEREXAMPLE_EXTRACTION,
            description=(
                "Convert raw solver output (SAT/SMT models) into first-class "
                "ObstructionRecord objects with cohomological metadata."
            ),
            theory_reference="Ch11 §11.2 — counterexample_minimality (Thm 11.1)",
            is_required=True,
            depends_on=(),
        ),
        RepairSemanticsCapability(
            cap=RepairSemanticsCap.COHOMOLOGY_CLASSIFICATION,
            description=(
                "Classify an extracted counterexample as a Čech 1-cohomology "
                "class in Ȟ¹(𝔘, 𝒟) over the relevant covering."
            ),
            theory_reference="Ch11 §11.3 — cohomology_class_consistency (Thm 11.6)",
            is_required=True,
            depends_on=(RepairSemanticsCap.COUNTEREXAMPLE_EXTRACTION,),
        ),
        RepairSemanticsCapability(
            cap=RepairSemanticsCap.FRONTIER_MINIMIZATION,
            description=(
                "Compute the minimal set of coordinates whose local sections "
                "must be replaced to eliminate the cohomological obstruction."
            ),
            theory_reference="Ch11 §11.4 — frontier_minimality (Thm 11.4)",
            is_required=True,
            depends_on=(
                RepairSemanticsCap.COUNTEREXAMPLE_EXTRACTION,
                RepairSemanticsCap.COHOMOLOGY_CLASSIFICATION,
            ),
        ),
        RepairSemanticsCapability(
            cap=RepairSemanticsCap.REPAIR_PLANNING,
            description=(
                "Enumerate repair steps and their topological dependency order "
                "given the minimal repair frontier."
            ),
            theory_reference="Ch11 §11.5 — repair_admissibility (Thm 11.2)",
            is_required=True,
            depends_on=(
                RepairSemanticsCap.FRONTIER_MINIMIZATION,
            ),
        ),
        RepairSemanticsCapability(
            cap=RepairSemanticsCap.REPAIR_EXECUTION,
            description=(
                "Apply the section replacements prescribed by a RepairPlan, "
                "updating the sheaf in-place on the frontier patches."
            ),
            theory_reference=(
                "Ch11 §11.6 — local_section_replacement_soundness (Thm 11.8)"
            ),
            is_required=True,
            depends_on=(RepairSemanticsCap.REPAIR_PLANNING,),
        ),
        RepairSemanticsCapability(
            cap=RepairSemanticsCap.DESCENT_VALIDATION,
            description=(
                "Certify that the repaired sheaf satisfies all pairwise descent "
                "conditions on the frontier and its neighbourhood."
            ),
            theory_reference="Ch11 §11.7 — descent_preservation (Thm 11.3)",
            is_required=True,
            depends_on=(RepairSemanticsCap.REPAIR_EXECUTION,),
        ),
        RepairSemanticsCapability(
            cap=RepairSemanticsCap.CONVERGENCE_CHECKING,
            description=(
                "Detect whether the repair loop has reached a fixpoint or is "
                "diverging, using the bound from Thm 11.5."
            ),
            theory_reference="Ch11 §11.8 — repair_convergence (Thm 11.5)",
            is_required=True,
            depends_on=(
                RepairSemanticsCap.DESCENT_VALIDATION,
                RepairSemanticsCap.SESSION_TRACKING,
            ),
        ),
        RepairSemanticsCapability(
            cap=RepairSemanticsCap.SESSION_TRACKING,
            description=(
                "Persist the debug session's iteration history so that "
                "monotonicity of counterexample accumulation can be enforced."
            ),
            theory_reference="Ch11 §11.9 — session_monotonicity (Thm 11.7)",
            is_required=False,
            depends_on=(),
        ),
        RepairSemanticsCapability(
            cap=RepairSemanticsCap.DEBUG_ORCHESTRATION,
            description=(
                "Drive the full counterexample-extract → plan → execute → "
                "validate → check-convergence loop until a certificate is "
                "emitted or abandonment is triggered."
            ),
            theory_reference="Ch11 §11.10 — repair_convergence (Thm 11.5)",
            is_required=False,
            depends_on=(
                RepairSemanticsCap.COUNTEREXAMPLE_EXTRACTION,
                RepairSemanticsCap.REPAIR_PLANNING,
                RepairSemanticsCap.REPAIR_EXECUTION,
                RepairSemanticsCap.DESCENT_VALIDATION,
                RepairSemanticsCap.CONVERGENCE_CHECKING,
                RepairSemanticsCap.SESSION_TRACKING,
            ),
        ),
        RepairSemanticsCapability(
            cap=RepairSemanticsCap.REPAIR_CERTIFICATE_EMISSION,
            description=(
                "Emit a machine-verifiable proof certificate once the repair "
                "loop converges, suitable for archival and external audit."
            ),
            theory_reference="Ch11 §11.11 — repair_admissibility (Thm 11.2)",
            is_required=False,
            depends_on=(
                RepairSemanticsCap.DESCENT_VALIDATION,
                RepairSemanticsCap.CONVERGENCE_CHECKING,
            ),
        ),
    )


def _validate_capability_graph(
    caps: tuple[RepairSemanticsCapability, ...],
) -> list[str]:
    """Check that the capability dependency graph contains no cycles.

    Uses depth-first search with a grey/black colouring scheme.  A *grey*
    node is currently on the DFS stack; a *black* node has been fully
    explored.  A back-edge to a grey node indicates a cycle.

    Parameters
    ----------
    caps:
        The full tuple of capabilities to validate.

    Returns
    -------
    list[str]
        An empty list if the graph is acyclic; otherwise a list of error
        strings naming the detected cycles.
    """
    # Build adjacency list
    cap_map: dict[RepairSemanticsCap, set[RepairSemanticsCap]] = {
        c.cap: set(c.depends_on) for c in caps
    }

    grey: set[RepairSemanticsCap] = set()
    black: set[RepairSemanticsCap] = set()
    errors: list[str] = []

    def dfs(node: RepairSemanticsCap) -> None:
        if node in black:
            return
        if node in grey:
            errors.append(
                f"Cycle detected in capability graph involving {node.value!r}."
            )
            return
        grey.add(node)
        for neighbour in cap_map.get(node, set()):
            dfs(neighbour)
        grey.discard(node)
        black.add(node)

    for cap in cap_map:
        dfs(cap)

    return errors


def _check_theorem_coverage(manifest: RepairSemanticsManifest) -> list[str]:
    """Check that every declared theorem target is cited by at least one capability.

    A theorem target is *covered* if its name appears as a substring of any
    capability's ``theory_reference`` field.

    Parameters
    ----------
    manifest:
        The manifest to check.

    Returns
    -------
    list[str]
        A list of warning strings for uncovered theorem targets.  An empty
        list means every theorem has at least one capability referencing it.
    """
    warnings: list[str] = []
    all_refs = " ".join(c.theory_reference for c in manifest.capabilities)
    for thm in manifest.theorem_targets:
        if thm not in all_refs:
            warnings.append(
                f"Theorem target {thm!r} is not cited by any capability's "
                "theory_reference field."
            )
    return warnings


# ---------------------------------------------------------------------------
# Capability registry
# ---------------------------------------------------------------------------

# _CAPABILITY_REGISTRY maps each RepairSemanticsCap to its full capability
# descriptor.  This registry is built once at module load and is used by the
# manifest singleton and by external code that needs per-capability metadata
# without holding a reference to the full manifest.

_CAPABILITY_REGISTRY: dict[RepairSemanticsCap, RepairSemanticsCapability] = {
    c.cap: c for c in _build_capabilities()
}


# ---------------------------------------------------------------------------
# Manifest singleton
# ---------------------------------------------------------------------------

REPAIR_SEMANTICS_MANIFEST: RepairSemanticsManifest = RepairSemanticsManifest(
    spec_provenance=MANIFEST_SPEC_PROVENANCE,
    theorem_targets=THEOREM_TARGETS,
    capabilities=_build_capabilities(),
    subsystem_name="repair_semantics",
    version="1.0.0",
    description=(
        "Implements the Ch11 debugging pipeline: counterexample extraction, "
        "repair planning, repair execution, and debug orchestration based on "
        "the local-section-replacement model with Čech cohomological "
        "obstruction classification."
    ),
    theory_chapter="Ch11 — Debugging, Counterexamples, and Repair",
    repair_model="local-section-replacement-with-descent-check",
    counterexample_model="cohomology-class-H1",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_manifest() -> RepairSemanticsManifest:
    """Return the repair_semantics subsystem manifest singleton.

    This function is the canonical entry point for consumers that need the
    manifest.  It returns the pre-built ``REPAIR_SEMANTICS_MANIFEST``
    instance without performing any additional computation.

    Returns
    -------
    RepairSemanticsManifest
        The singleton manifest for the repair_semantics subsystem.

    Examples
    --------
    >>> m = get_manifest()
    >>> m.subsystem_name
    'repair_semantics'
    >>> m.has_capability(RepairSemanticsCap.REPAIR_PLANNING)
    True
    """
    return REPAIR_SEMANTICS_MANIFEST


def validate_manifest() -> list[str]:
    """Run all validation checks on the manifest singleton and return errors.

    Performs the full suite of checks defined in
    ``RepairSemanticsManifest.validate`` plus the theorem-coverage check
    from ``_check_theorem_coverage``.

    Returns
    -------
    list[str]
        An empty list if the manifest is fully valid; otherwise a list of
        human-readable error strings.  Callers SHOULD treat a non-empty
        return value as a fatal misconfiguration.

    Examples
    --------
    >>> errors = validate_manifest()
    >>> errors
    []
    """
    m = REPAIR_SEMANTICS_MANIFEST
    errors = m.validate()
    coverage_warnings = _check_theorem_coverage(m)
    # Treat coverage warnings as errors: every theorem target must be cited.
    errors.extend(coverage_warnings)
    return errors


def manifest_to_dict() -> dict[str, Any]:
    """Serialise the manifest singleton to a plain-Python dict.

    Returns
    -------
    dict[str, Any]
        A JSON-serialisable dictionary representation of the manifest,
        suitable for writing to a manifest.json file or embedding in a
        telemetry payload.

    Examples
    --------
    >>> d = manifest_to_dict()
    >>> d["subsystem_name"]
    'repair_semantics'
    >>> isinstance(d["capabilities"], list)
    True
    """
    return REPAIR_SEMANTICS_MANIFEST.to_dict()


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "MANIFEST_SPEC_PROVENANCE",
    "THEOREM_TARGETS",
    "RepairSemanticsCap",
    "RepairSemanticsCapability",
    "RepairSemanticsManifest",
    "REPAIR_SEMANTICS_MANIFEST",
    "get_manifest",
    "validate_manifest",
    "manifest_to_dict",
]
# copilot: end of repair_semantics manifest
