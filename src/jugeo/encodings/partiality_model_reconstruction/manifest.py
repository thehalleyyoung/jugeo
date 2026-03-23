r"""Package manifest for partiality_model_reconstruction, Ch31 of theory2.tex.

Tracks all components, their kinds, versions, statuses, dependencies, and
exports.  Used by the build system and at runtime to validate the package.

.. math::

   \text{Component} = (\text{name}, \text{kind}, \text{version},
                        \text{status}, \text{deps}, \text{exports})
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import dataclasses
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

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
# Enumerations
# ---------------------------------------------------------------------------


class ManifestStatus(str, Enum):
    """Lifecycle status of a manifest component.

    Components move forward through the lifecycle:
    DRAFT -> ACTIVE -> DEPRECATED -> ARCHIVED.
    Active components are fully usable; deprecated ones are kept for
    backward compatibility; archived ones are read-only historical artefacts.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class ComponentKind(str, Enum):
    """Coarse classification of what a component *is*.

    MODEL        — primary data-structure definitions
    ALGORITHM    — computational procedures
    SECTION      — a chapter section (§31.x)
    INTEGRATION  — bridge to external subsystems (solver, evidence, …)
    THEOREM      — formal theorem statements and proof objects
    MANIFEST     — the manifest itself
    """

    MODEL = "model"
    ALGORITHM = "algorithm"
    SECTION = "section"
    INTEGRATION = "integration"
    THEOREM = "theorem"
    MANIFEST = "manifest"


# ---------------------------------------------------------------------------
# ComponentRecord — immutable, frozen dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComponentRecord:
    """Immutable record describing one component in the package.

    Each record carries the canonical name, the kind of component,
    the fully-qualified module path, a human-readable description,
    a semantic version string, the current lifecycle status, an ordered
    tuple of dependency names, and the tuple of public export names.

    Because the dataclass is *frozen*, modifications produce new instances
    via :func:`dataclasses.replace` — never mutate the original.

    .. math::

       \\text{ComponentRecord} = (\\text{name}, \\text{kind}, \\ldots)

    Parameters
    ----------
    name:
        Short snake_case identifier used as the registry key.
    kind:
        Coarse classification (model, algorithm, section, …).
    module_path:
        Fully qualified Python module path.
    description:
        One-sentence human-readable description.
    version:
        Semantic version string, e.g. ``"0.1.0"``.
    status:
        Current lifecycle status.
    dependencies:
        Ordered tuple of dependency *names* (other component names).
    exports:
        Ordered tuple of exported symbol names.
    """

    # Core identification
    name: str
    kind: ComponentKind
    module_path: str
    description: str
    version: str
    status: ManifestStatus

    # Optional relational metadata
    dependencies: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()

    # Class-level constant — not a field
    _VERSION_PREFIX: ClassVar[str] = "jugeo-component"

    # ---------------------------------------------------------------------------
    # Status helpers
    # ---------------------------------------------------------------------------

    def is_active(self) -> bool:
        """Return ``True`` iff this component has ACTIVE status.

        An active component is fully production-ready and may be depended on
        by other active components.
        """
        return self.status == ManifestStatus.ACTIVE

    def is_deprecated(self) -> bool:
        """Return ``True`` iff this component has DEPRECATED status.

        Deprecated components still work but should not be used in new code.
        Dependents should migrate to the replacement.
        """
        return self.status == ManifestStatus.DEPRECATED

    # ---------------------------------------------------------------------------
    # Cardinality helpers
    # ---------------------------------------------------------------------------

    def dependency_count(self) -> int:
        """Return the number of direct dependencies declared by this component."""
        return len(self.dependencies)

    def export_count(self) -> int:
        """Return the number of public symbols this component exports."""
        return len(self.exports)

    # ---------------------------------------------------------------------------
    # Serialization
    # ---------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize this record to a plain JSON-compatible dict.

        All enum fields are serialized to their string ``.value``; tuples are
        converted to lists so that the result is directly ``json.dumps``-able.

        Returns
        -------
        dict[str, Any]
            A plain dict containing all fields.
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "module_path": self.module_path,
            "description": self.description,
            "version": self.version,
            "status": self.status.value,
            # Tuples are not JSON-serializable — convert to list
            "dependencies": list(self.dependencies),
            "exports": list(self.exports),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ComponentRecord:
        """Deserialize a :class:`ComponentRecord` from a plain dict.

        Expects the same keys that :meth:`to_dict` produces.  Enum values
        are reconstructed from their string representations.

        Parameters
        ----------
        d:
            Dict previously produced by :meth:`to_dict`.

        Returns
        -------
        ComponentRecord
            A new, fully-populated record.

        Raises
        ------
        KeyError
            If a required field is missing from *d*.
        ValueError
            If an enum value is unrecognized.
        """
        return cls(
            name=d["name"],
            kind=ComponentKind(d["kind"]),
            module_path=d["module_path"],
            description=d["description"],
            version=d["version"],
            status=ManifestStatus(d["status"]),
            # JSON stores these as lists; convert back to tuple
            dependencies=tuple(d.get("dependencies", [])),
            exports=tuple(d.get("exports", [])),
        )

    # ---------------------------------------------------------------------------
    # Mutation-by-copy helpers (frozen dataclass pattern)
    # ---------------------------------------------------------------------------

    def with_status(self, status: ManifestStatus) -> ComponentRecord:
        """Return a new :class:`ComponentRecord` identical to *self* but with a
        different ``status``.

        Uses :func:`dataclasses.replace` to produce the copy without breaking
        the frozen invariant on the original.

        Parameters
        ----------
        status:
            The desired new lifecycle status.

        Returns
        -------
        ComponentRecord
            A new record with the updated status.
        """
        # dataclasses.replace is the canonical way to "modify" frozen dataclasses
        return dataclasses.replace(self, status=status)

    # ---------------------------------------------------------------------------
    # Display helpers
    # ---------------------------------------------------------------------------

    def summary_line(self) -> str:
        """Return a single compact summary line suitable for CLI output.

        Format: ``[kind] name vVERSION (status) — description[:60]``

        Returns
        -------
        str
            A human-readable one-liner.
        """
        # Truncate long descriptions to 60 characters for readability
        truncated_desc = self.description[:60]
        return (
            f"[{self.kind.value}] {self.name} v{self.version}"
            f" ({self.status.value}) — {truncated_desc}"
        )


# ---------------------------------------------------------------------------
# PackageManifest — registry of all ComponentRecords
# ---------------------------------------------------------------------------


class PackageManifest:
    """Central registry of all components in the ``partiality_model_reconstruction``
    package (theory2.tex Ch31).

    At construction time every known component is pre-registered via
    :meth:`register`.  The manifest is then used both by the build system
    (to validate consistency) and at runtime (to resolve dependencies and
    discover exports).

    The internal store is a plain dict mapping component *name* to
    :class:`ComponentRecord`.  Thread-safety is **not** guaranteed; the
    manifest is intended to be constructed once (module import time) and
    then treated as read-only.
    """

    def __init__(self) -> None:
        """Initialise the manifest and pre-register all Ch31 components."""
        # Primary storage: name -> ComponentRecord
        self._records: dict[str, ComponentRecord] = {}

        # ------------------------------------------------------------------
        # 1. manifest  — the manifest itself
        # ------------------------------------------------------------------
        self.register(ComponentRecord(
            name="manifest",
            kind=ComponentKind.MANIFEST,
            module_path="jugeo.encodings.partiality_model_reconstruction.manifest",
            description=(
                "Package manifest and component registry for Ch31 "
                "partiality model reconstruction"
            ),
            version="0.1.0",
            status=ManifestStatus.ACTIVE,
            dependencies=(),
            exports=(
                "PackageManifest",
                "ComponentRecord",
                "ManifestStatus",
                "ComponentKind",
                "ManifestValidator",
                "PACKAGE_MANIFEST",
            ),
        ))

        # ------------------------------------------------------------------
        # 2. models  — core data structures
        # ------------------------------------------------------------------
        self.register(ComponentRecord(
            name="models",
            kind=ComponentKind.MODEL,
            module_path="jugeo.encodings.partiality_model_reconstruction.models",
            description=(
                "Core data models: PartialFunctionEncoding, "
                "ExceptionValuedSemantics, AlgebraicSurface, "
                "ModelReconstruction, BranchSensitivity"
            ),
            version="0.1.0",
            status=ManifestStatus.ACTIVE,
            dependencies=("manifest",),
            exports=(
                "PartialFunctionEncoding",
                "ExceptionValuedSemantics",
                "AlgebraicSurface",
                "ModelReconstruction",
                "BranchSensitivity",
                "PartialityKind",
                "ExceptionKind",
                "ReconstructionStatus",
                "TrustAnnotationKind",
            ),
        ))

        # ------------------------------------------------------------------
        # 3. partial_functions  — §31.1
        # ------------------------------------------------------------------
        self.register(ComponentRecord(
            name="partial_functions",
            kind=ComponentKind.SECTION,
            module_path=(
                "jugeo.encodings.partiality_model_reconstruction.partial_functions"
            ),
            description=(
                "§31.1 Partial functions as Z3 relations with domain predicates"
            ),
            version="0.1.0",
            status=ManifestStatus.ACTIVE,
            dependencies=("models",),
            exports=(
                "DomainPredicate",
                "PartialFunctionLattice",
                "GuardedEncoding",
                "TotalizationStrategy",
                "DomainPredicateKind",
                "TotalizationKind",
                "CompositionMode",
            ),
        ))

        # ------------------------------------------------------------------
        # 4. exception_semantics  — §31.2
        # ------------------------------------------------------------------
        self.register(ComponentRecord(
            name="exception_semantics",
            kind=ComponentKind.SECTION,
            module_path=(
                "jugeo.encodings.partiality_model_reconstruction.exception_semantics"
            ),
            description=(
                "§31.2 Exception-valued semantics and sum type encodings"
            ),
            version="0.1.0",
            status=ManifestStatus.ACTIVE,
            dependencies=("models",),
            exports=(
                "ExceptionSort",
                "MaybeEncoding",
                "EitherEncoding",
                "ExceptionPropagationGraph",
                "PropagationRule",
                "SumTypeKind",
            ),
        ))

        # ------------------------------------------------------------------
        # 5. algebraic_surfaces  — §31.3
        # ------------------------------------------------------------------
        self.register(ComponentRecord(
            name="algebraic_surfaces",
            kind=ComponentKind.SECTION,
            module_path=(
                "jugeo.encodings.partiality_model_reconstruction.algebraic_surfaces"
            ),
            description="§31.3 Algebraic data type surfaces in Z3",
            version="0.1.0",
            status=ManifestStatus.ACTIVE,
            dependencies=("models",),
            exports=(
                "ConstructorSpec",
                "RecognizerPredicate",
                "AlgebraicFold",
                "SurfaceProjection",
                "ConstructorArity",
                "SurfaceKind",
                "ProjectionMode",
            ),
        ))

        # ------------------------------------------------------------------
        # 6. model_reconstruction  — §31.4
        # ------------------------------------------------------------------
        self.register(ComponentRecord(
            name="model_reconstruction",
            kind=ComponentKind.SECTION,
            module_path=(
                "jugeo.encodings.partiality_model_reconstruction.model_reconstruction"
            ),
            description="§31.4 Full model reconstruction pipeline",
            version="0.1.0",
            status=ManifestStatus.ACTIVE,
            dependencies=(
                "models",
                "partial_functions",
                "exception_semantics",
                "algebraic_surfaces",
            ),
            exports=(
                "ReconstructionPipeline",
                "PartialModelAssembler",
                "TrustAnnotator",
                "EvidencePackager",
                "AssemblyPhase",
                "CompletionStrategy",
            ),
        ))

        # ------------------------------------------------------------------
        # 7. algorithms  — computational procedures
        # ------------------------------------------------------------------
        self.register(ComponentRecord(
            name="algorithms",
            kind=ComponentKind.ALGORITHM,
            module_path=(
                "jugeo.encodings.partiality_model_reconstruction.algorithms"
            ),
            description=(
                "Core algorithms for partiality encoding and model reconstruction"
            ),
            version="0.1.0",
            status=ManifestStatus.ACTIVE,
            dependencies=(
                "models",
                "partial_functions",
                "exception_semantics",
                "algebraic_surfaces",
                "model_reconstruction",
            ),
            exports=(
                "AlgorithmResult",
                "AlgorithmRegistry",
                "encode_partial_function",
                "decode_z3_model_to_surface",
                "reconstruct_evidence_from_model",
                "compute_branch_sensitivity",
                "totalize_partial",
                "merge_reconstructed_models",
                "validate_model_faithfulness",
            ),
        ))

        # ------------------------------------------------------------------
        # 8. integration  — bridge to solver / evidence infrastructure
        # ------------------------------------------------------------------
        self.register(ComponentRecord(
            name="integration",
            kind=ComponentKind.INTEGRATION,
            module_path=(
                "jugeo.encodings.partiality_model_reconstruction.integration"
            ),
            description=(
                "Integration with JuGeo solver and evidence infrastructure"
            ),
            version="0.1.0",
            status=ManifestStatus.ACTIVE,
            dependencies=("models", "algorithms"),
            exports=(
                "PartialityEncodingSession",
                "ModelReconstructionPipeline",
                "ExceptionSemanticsBridge",
                "CopilotReconstructionAssist",
                "SessionState",
                "BridgeStatus",
            ),
        ))

        # ------------------------------------------------------------------
        # 9. theorems  — formal theorem statements + Z3 encodings
        # ------------------------------------------------------------------
        self.register(ComponentRecord(
            name="theorems",
            kind=ComponentKind.THEOREM,
            module_path=(
                "jugeo.encodings.partiality_model_reconstruction.theorems"
            ),
            description=(
                "Theorem statements, proof sketches, and Z3 encodings for Ch31"
            ),
            version="0.1.0",
            status=ManifestStatus.ACTIVE,
            dependencies=(
                "models",
                "partial_functions",
                "exception_semantics",
                "algebraic_surfaces",
                "model_reconstruction",
            ),
            exports=(
                "Theorem",
                "TheoremRegistry",
                "VerificationStatus",
                "TheoremKind",
                "THEOREM_TOTALITY_UNDER_RESTRICTION",
                "THEOREM_EXCEPTION_PROPAGATION_MONOTONICITY",
                "THEOREM_ALGEBRAIC_SURFACE_FAITHFULNESS",
                "THEOREM_MODEL_RECONSTRUCTION_SOUNDNESS",
                "THEOREM_BRANCH_SENSITIVITY_CORRECTNESS",
                "THEOREM_REGISTRY",
            ),
        ))

    # ---------------------------------------------------------------------------
    # Core registry operations
    # ---------------------------------------------------------------------------

    def register(self, record: ComponentRecord) -> None:
        """Add (or replace) a :class:`ComponentRecord` in the registry.

        The record is stored under ``record.name`` as the key.  If a record
        with the same name already exists it is silently overwritten, which
        allows re-registration during testing or hot-reload scenarios.

        Parameters
        ----------
        record:
            The component record to store.
        """
        # Direct dict insert — O(1) average
        self._records[record.name] = record

    def lookup(self, name: str) -> ComponentRecord | None:
        """Look up a component by name.

        Parameters
        ----------
        name:
            The snake_case component name to search for.

        Returns
        -------
        ComponentRecord | None
            The matching record, or ``None`` if not found.
        """
        # .get returns None automatically when the key is absent
        return self._records.get(name)

    # ---------------------------------------------------------------------------
    # Filtering helpers
    # ---------------------------------------------------------------------------

    def by_kind(self, kind: ComponentKind) -> list[ComponentRecord]:
        """Return all records whose ``kind`` matches *kind*.

        Parameters
        ----------
        kind:
            The :class:`ComponentKind` to filter on.

        Returns
        -------
        list[ComponentRecord]
            Possibly empty list of matching records, in insertion order.
        """
        # Linear scan — the registry is small enough that this is fine
        return [r for r in self._records.values() if r.kind == kind]

    def active_components(self) -> list[ComponentRecord]:
        """Return all components currently in ACTIVE status.

        Returns
        -------
        list[ComponentRecord]
            Ordered list of active records.
        """
        return [r for r in self._records.values() if r.is_active()]

    def deprecated_components(self) -> list[ComponentRecord]:
        """Return all components currently in DEPRECATED status.

        Returns
        -------
        list[ComponentRecord]
            Ordered list of deprecated records.
        """
        return [r for r in self._records.values() if r.is_deprecated()]

    # ---------------------------------------------------------------------------
    # Dependency graph
    # ---------------------------------------------------------------------------

    def dependency_graph(self) -> dict[str, list[str]]:
        """Compute the full dependency graph as an adjacency list.

        Each key is a component name; the corresponding value is the list of
        names that component directly depends on.

        Returns
        -------
        dict[str, list[str]]
            Adjacency-list representation of the dependency DAG.
        """
        return {name: list(rec.dependencies) for name, rec in self._records.items()}

    # ---------------------------------------------------------------------------
    # Cycle detection
    # ---------------------------------------------------------------------------

    def check_circular_deps(self) -> list[str]:
        """Detect circular dependencies using iterative DFS with a colour map.

        Nodes are coloured WHITE (unvisited), GRAY (on the current DFS stack),
        or BLACK (fully explored).  A back-edge to a GRAY node signals a cycle.

        Returns
        -------
        list[str]
            Human-readable descriptions of every cycle found, e.g.
            ``"a -> b -> c -> a"``.  Empty list means the graph is acyclic.
        """
        # Colour constants — classic DFS colouring
        WHITE, GRAY, BLACK = 0, 1, 2

        # Initialise all nodes as WHITE (unvisited)
        colour: dict[str, int] = {name: WHITE for name in self._records}
        # parent map for path reconstruction
        parent: dict[str, str | None] = {name: None for name in self._records}
        # Collect cycle descriptions here
        cycles: list[str] = []
        # Adjacency list for convenient traversal
        graph = self.dependency_graph()

        def _reconstruct_cycle(start: str, end: str) -> str:
            """Walk the parent chain from *end* back to *start* to produce a
            readable cycle description string."""
            path: list[str] = [end]
            current: str | None = parent.get(end)
            # Walk backwards until we reach the start node (or exhaust parents)
            visited_in_walk: set[str] = {end}
            while current is not None and current != start:
                if current in visited_in_walk:
                    # Guard against infinite loop in malformed parent map
                    break
                path.append(current)
                visited_in_walk.add(current)
                current = parent.get(current)
            path.append(start)
            path.reverse()
            return " -> ".join(path)

        def _dfs(node: str) -> None:
            """Depth-first search from *node*, updating colour and parent."""
            colour[node] = GRAY  # Mark as "on current stack"
            for neighbour in graph.get(node, []):
                if neighbour not in colour:
                    # Neighbour is not even in the registry — skip (validated elsewhere)
                    continue
                if colour[neighbour] == GRAY:
                    # Back edge detected: we have found a cycle
                    cycle_desc = _reconstruct_cycle(neighbour, node)
                    cycles.append(f"Cycle: {cycle_desc}")
                elif colour[neighbour] == WHITE:
                    # Tree edge: recurse
                    parent[neighbour] = node
                    _dfs(neighbour)
            colour[node] = BLACK  # Mark as fully explored

        # Run DFS from every unvisited node (handles disconnected subgraphs)
        for node_name in list(self._records.keys()):
            if colour[node_name] == WHITE:
                _dfs(node_name)

        return cycles

    # ---------------------------------------------------------------------------
    # Export map
    # ---------------------------------------------------------------------------

    def export_all(self) -> dict[str, tuple[str, ...]]:
        """Return a mapping of component name to its exports tuple.

        Returns
        -------
        dict[str, tuple[str, ...]]
            ``{name: (export1, export2, …)}`` for every registered component.
        """
        return {name: rec.exports for name, rec in self._records.items()}

    # ---------------------------------------------------------------------------
    # JSON serialization
    # ---------------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialize the entire manifest to a JSON string.

        Each record is converted via :meth:`ComponentRecord.to_dict` before
        being passed to :func:`json.dumps`.  The output is pretty-printed with
        a 2-space indent for readability.

        Returns
        -------
        str
            A valid JSON string representing all registered components.
        """
        # Build list of dicts — one per record, in insertion order
        records_list = [rec.to_dict() for rec in self._records.values()]
        payload = {
            # Include a format-version key so future readers can migrate
            "format_version": "1",
            "generated_at": time.time(),
            "components": records_list,
        }
        return json.dumps(payload, indent=2)

    @classmethod
    def from_json(cls, s: str) -> PackageManifest:
        """Deserialize a :class:`PackageManifest` from a JSON string.

        Creates a *new* manifest with an empty registry, then populates it
        by deserializing each record in the JSON ``"components"`` list.

        Parameters
        ----------
        s:
            A JSON string previously produced by :meth:`to_json`.

        Returns
        -------
        PackageManifest
            A freshly constructed manifest populated from *s*.

        Raises
        ------
        json.JSONDecodeError
            If *s* is not valid JSON.
        KeyError
            If a required field is missing from a record dict.
        """
        payload = json.loads(s)
        # Create a blank manifest — we intentionally skip __init__ pre-registration
        # by constructing via __new__ and then manually resetting _records.
        instance = cls.__new__(cls)
        # Reset internal storage before deserializing
        instance._records = {}
        # Deserialize each component record and register it
        for rec_dict in payload.get("components", []):
            record = ComponentRecord.from_dict(rec_dict)
            instance.register(record)
        return instance

    # ---------------------------------------------------------------------------
    # Validation
    # ---------------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Run internal consistency checks and return a list of error messages.

        Checks performed:
        1. All dependency names referenced by any record must themselves be
           registered in the manifest.
        2. Every ACTIVE component must have at least one export.
        3. The dependency graph must be acyclic.

        Returns
        -------
        list[str]
            A (possibly empty) list of human-readable error descriptions.
            An empty list means the manifest is consistent.
        """
        errors: list[str] = []

        for name, rec in self._records.items():
            # --- Check 1: all declared dependencies must be registered ---
            for dep in rec.dependencies:
                if dep not in self._records:
                    errors.append(
                        f"Component '{name}' declares dependency '{dep}' "
                        f"which is not registered in the manifest."
                    )

            # --- Check 2: active components must have exports ---
            if rec.is_active() and rec.export_count() == 0:
                errors.append(
                    f"ACTIVE component '{name}' has no exports declared."
                )

        # --- Check 3: no circular dependencies ---
        cycle_errors = self.check_circular_deps()
        errors.extend(cycle_errors)

        return errors

    # ---------------------------------------------------------------------------
    # Summary and iteration helpers
    # ---------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a high-level statistical summary of the manifest.

        Returns
        -------
        dict[str, Any]
            A dict containing:
            - ``total_count``: total number of registered components
            - ``active_count``: number of ACTIVE components
            - ``deprecated_count``: number of DEPRECATED components
            - ``kinds``: dict mapping each :class:`ComponentKind` value to its count
        """
        # Tally counts by kind using a plain dict
        kind_counts: dict[str, int] = {k.value: 0 for k in ComponentKind}
        for rec in self._records.values():
            kind_counts[rec.kind.value] += 1

        return {
            "total_count": len(self._records),
            "active_count": len(self.active_components()),
            "deprecated_count": len(self.deprecated_components()),
            "kinds": kind_counts,
        }

    def iter_by_status(self) -> dict[str, list[ComponentRecord]]:
        """Return all records grouped by their lifecycle status.

        Returns
        -------
        dict[str, list[ComponentRecord]]
            A dict keyed by status string value (``"draft"``, ``"active"``,
            etc.) mapping to the list of records with that status.
        """
        # Initialise a bucket for each known status value
        buckets: dict[str, list[ComponentRecord]] = {
            s.value: [] for s in ManifestStatus
        }
        for rec in self._records.values():
            buckets[rec.status.value].append(rec)
        return buckets

    def __repr__(self) -> str:
        """Return a terse debugging representation."""
        n = len(self._records)
        return f"PackageManifest(components={n})"

    def __len__(self) -> int:
        """Return the number of registered components."""
        return len(self._records)


# ---------------------------------------------------------------------------
# ManifestValidator — static validation helpers
# ---------------------------------------------------------------------------


class ManifestValidator:
    """Collection of static validation methods for :class:`PackageManifest`.

    All methods are static and take a :class:`PackageManifest` instance as
    their sole argument, returning a (possibly empty) list of error strings.
    Collecting rather than raising exceptions allows callers to accumulate all
    errors in a single pass and report them together.
    """

    @staticmethod
    def validate_no_cycles(manifest: PackageManifest) -> list[str]:
        """Check that the dependency graph contains no cycles.

        Delegates to :meth:`PackageManifest.check_circular_deps`.

        Parameters
        ----------
        manifest:
            The manifest to inspect.

        Returns
        -------
        list[str]
            Cycle descriptions, or an empty list if the graph is acyclic.
        """
        # Delegate to the manifest's own cycle-detection logic
        return manifest.check_circular_deps()

    @staticmethod
    def validate_all_deps_present(manifest: PackageManifest) -> list[str]:
        """Verify that every dependency name referenced anywhere is registered.

        Iterates over all records and checks each declared dependency against
        the registry.  Accumulates one error string per missing dependency.

        Parameters
        ----------
        manifest:
            The manifest to inspect.

        Returns
        -------
        list[str]
            Error descriptions for any unregistered dependency, or ``[]``.
        """
        errors: list[str] = []
        for name, rec in manifest._records.items():
            for dep_name in rec.dependencies:
                if manifest.lookup(dep_name) is None:
                    errors.append(
                        f"'{name}' depends on '{dep_name}' which is not in the manifest."
                    )
        return errors

    @staticmethod
    def validate_exports_non_empty(manifest: PackageManifest) -> list[str]:
        """Verify that every ACTIVE component declares at least one export.

        Components in other lifecycle states (DRAFT, DEPRECATED, ARCHIVED) are
        exempt — they may legitimately have empty export lists.

        Parameters
        ----------
        manifest:
            The manifest to inspect.

        Returns
        -------
        list[str]
            Error descriptions for each ACTIVE component with no exports.
        """
        errors: list[str] = []
        for name, rec in manifest._records.items():
            # Only enforce the non-empty export rule for ACTIVE components
            if rec.is_active() and rec.export_count() == 0:
                errors.append(
                    f"ACTIVE component '{name}' has an empty exports tuple."
                )
        return errors

    @staticmethod
    def full_validation(manifest: PackageManifest) -> list[str]:
        """Run all available validation checks and combine their results.

        Runs, in order:
        1. :meth:`validate_no_cycles`
        2. :meth:`validate_all_deps_present`
        3. :meth:`validate_exports_non_empty`

        Parameters
        ----------
        manifest:
            The manifest to validate.

        Returns
        -------
        list[str]
            Combined, deduplicated list of all error messages found.
            An empty list means all checks passed.
        """
        # Run each validator and extend the aggregate list
        all_errors: list[str] = []
        all_errors.extend(ManifestValidator.validate_no_cycles(manifest))
        all_errors.extend(ManifestValidator.validate_all_deps_present(manifest))
        all_errors.extend(ManifestValidator.validate_exports_non_empty(manifest))

        # Deduplicate while preserving order (dict trick, Python 3.7+)
        seen: dict[str, None] = {}
        for err in all_errors:
            seen[err] = None
        return list(seen.keys())


# ---------------------------------------------------------------------------
# Module-level singleton — created once at import time
# ---------------------------------------------------------------------------

PACKAGE_MANIFEST: PackageManifest = PackageManifest()
"""The canonical package manifest singleton for Ch31.

Import and use this instance directly rather than constructing new
:class:`PackageManifest` objects, unless you need an isolated registry
for testing purposes.

Example::

    from jugeo.encodings.partiality_model_reconstruction.manifest import (
        PACKAGE_MANIFEST,
    )
    rec = PACKAGE_MANIFEST.lookup("models")
    assert rec is not None and rec.is_active()
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ManifestStatus",
    "ComponentKind",
    "ComponentRecord",
    "PackageManifest",
    "ManifestValidator",
    "PACKAGE_MANIFEST",
]
