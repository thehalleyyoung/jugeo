from __future__ import annotations

r"""Import Graph Data Models — theory2.tex Ch19 §19.1.

This module provides the canonical frozen dataclass hierarchy used to represent
the import graph of a Python runtime as a Grothendieck site.  Every module
becomes an :class:`ImportNode` (a coordinate), every import statement becomes an
:class:`ImportEdge` (a morphism), and the transitive closure of a package's
internal edges yields a :class:`PackageFixedPoint` encoding the sheaf-theoretic
fixed point of the package boundary.

Design principles
-----------------
1. **Immutability first** — all primary records are ``frozen=True, slots=True``
   dataclasses.  Updates are performed via :func:`dataclasses.replace` to
   preserve the algebraic invariants required by theory2.tex §19.1.
2. **Trust is explicit** — every node and edge carries a :class:`TrustLevel`.
   Copilot-assisted scaffolding always enters at ``ORACLE_PROPOSED``; promotion
   to ``SOLVER_DISCHARGED`` or higher requires an explicit policy step.
3. **Jugeo geometry is optional** — all jugeo imports are wrapped in
   ``try/except ImportError`` so this module can be used in standalone mode
   with lightweight stubs.

Theory alignment
----------------
* §19.1 — Import Graph Objects (ImportNode, ImportEdge)
* §19.2 — Package Fixed Points (PackageFixedPoint)
* §19.3 — Dynamic Loading (DynamicLoadRecord)
* §19.4 — Re-exports and Name Transport (ReExportMap)

The word *copilot* appears throughout because copilot assistance is the primary
evidence channel for import-graph analysis; trust levels constrain how far
copilot-produced edges may propagate without external discharge.
"""

import hashlib
import json
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

# ---
# Jugeo geometry imports (with stubs for standalone usage)
# ---

try:
    from jugeo.geometry.site import (
        Coordinate,
        CoordinateKind,
        CoveringFamily,
        GrothendieckTopology,
        Morphism,
        MorphismKind,
        Site,
        SiteBuilder,
        CoordinateObject,
    )
except ImportError:

    class CoordinateKind:  # type: ignore[no-redef]
        MODULE = "MODULE"
        FUNCTION = "FUNCTION"
        INTERFACE = "INTERFACE"
        TEST = "TEST"
        THEOREM = "THEOREM"
        REGION = "REGION"

    class MorphismKind:  # type: ignore[no-redef]
        RESTRICTION = "RESTRICTION"
        INCLUSION = "INCLUSION"
        TRANSPORT = "TRANSPORT"
        REFINEMENT = "REFINEMENT"

    @dataclass(frozen=True, slots=True)
    class Coordinate:  # type: ignore[no-redef]
        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: dict[str, Any] = field(default_factory=dict)

        @property
        def name(self) -> str:
            return ".".join(self.components) if self.components else "<root>"

    @dataclass(frozen=True, slots=True)
    class Morphism:  # type: ignore[no-redef]
        source: Any = None
        target: Any = None
        kind: Any = None
        label: str = ""

    @dataclass
    class CoveringFamily:  # type: ignore[no-redef]
        base: Any = None
        members: list[Any] = field(default_factory=list)
        label: str = ""

    @dataclass
    class GrothendieckTopology:  # type: ignore[no-redef]
        label: str = ""

        @staticmethod
        def discrete() -> "GrothendieckTopology":
            return GrothendieckTopology(label="discrete")

    @dataclass
    class Site:  # type: ignore[no-redef]
        label: str = ""

        def add_coordinate(self, coord: Any) -> None:
            pass

        def add_morphism(self, morphism: Any) -> None:
            pass

    class SiteBuilder:  # type: ignore[no-redef]
        pass

    CoordinateObject = Coordinate  # type: ignore[misc]

# ---
# Jugeo judgment imports (with stubs for standalone usage)
# ---

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        Judgment,
        JudgmentAlgebra,
        JudgmentBuilder,
        JudgmentStatus,
        LocalJudgment,
        Obstruction,
        PropositionKind,
        Proposition,
        Carrier,
        ProvenanceSource,
        Provenance,
        ResidualObligation,
        TrustAnnotation,
        TrustLevel,
        _now_iso,
        _stable_hash,
    )
except ImportError:
    from enum import IntEnum

    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

        def label(self) -> str:
            return self.name.lower().replace("_", "-")

    class EvidenceItemKind:  # type: ignore[no-redef]
        RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"
        SOLVER_PROOF = "solver_proof"

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        kind: Any = None
        payload: dict[str, Any] = field(default_factory=dict)
        trust_level: Any = None
        channel: str = ""
        timestamp: str = ""
        expiry: str = ""
        provenance: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        obstruction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        violated_condition: str = ""
        coordinate: str = ""
        evidence_at_time: tuple[str, ...] = ()
        repair_hints: tuple[str, ...] = ()
        cohomology_class: str = ""
        is_resolved: bool = False
        resolution_evidence: str = ""
        provenance: tuple[str, ...] = ()

    def _now_iso() -> str:  # type: ignore[no-redef]
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _stable_hash(payload: str) -> str:  # type: ignore[no-redef]
        return hashlib.sha256(payload.encode()).hexdigest()

    # Minimal stubs for types that are not otherwise used directly
    Judgment = object  # type: ignore[misc,assignment]
    LocalJudgment = object  # type: ignore[misc,assignment]
    JudgmentBuilder = object  # type: ignore[misc,assignment]
    JudgmentAlgebra = object  # type: ignore[misc,assignment]
    JudgmentStatus = object  # type: ignore[misc,assignment]
    PropositionKind = object  # type: ignore[misc,assignment]
    Proposition = object  # type: ignore[misc,assignment]
    Carrier = object  # type: ignore[misc,assignment]
    EvidenceBundle = object  # type: ignore[misc,assignment]
    ResidualObligation = object  # type: ignore[misc,assignment]
    TrustAnnotation = object  # type: ignore[misc,assignment]
    Provenance = object  # type: ignore[misc,assignment]
    ProvenanceSource = object  # type: ignore[misc,assignment]


# ---
# Module-level helpers
# ---

def _make_stub_coordinate(module_name: str) -> Coordinate:
    """Create a :class:`Coordinate` from a dotted module name.

    Each component of the dotted name becomes one path segment in the
    coordinate hierarchy (theory2.tex §19.1).  If the jugeo geometry package
    is unavailable, a stub :class:`Coordinate` is returned instead.

    The copilot integration layer calls this helper when constructing
    :class:`ImportNode` objects from ``sys.modules`` entries that have not
    yet been assigned a proper coordinate by the semantic site builder.

    Parameters
    ----------
    module_name:
        Dotted Python module name, e.g. ``"jugeo.python_runtime.import_graph"``.

    Returns
    -------
    Coordinate
        A coordinate whose ``components`` are the split dotted path segments.
    """
    components = tuple(module_name.split(".")) if module_name else ("<unknown>",)
    try:
        return Coordinate(
            components=components,
            kind=CoordinateKind.MODULE,
        )
    except Exception:
        return Coordinate(components=components)  # type: ignore[call-arg]


def _trust_value(t: Any) -> int:
    """Safely extract an integer value from a :class:`TrustLevel` or plain int.

    This helper centralises the coercion so that code using stub
    :class:`TrustLevel` instances (which may be plain ``IntEnum`` or raw
    integers) produces consistent results.  Copilot-generated nodes carry
    ``ORACLE_PROPOSED`` (value 2); this function lets callers compare trust
    levels without importing the full jugeo judgment stack.

    Parameters
    ----------
    t:
        A :class:`TrustLevel`, integer, or any object that supports ``int()``.

    Returns
    -------
    int
        The numeric trust level, or 0 (``CONTRADICTED``) if conversion fails.
    """
    try:
        return int(t)
    except (TypeError, ValueError):
        return 0


# ---
# §19.1 — ImportNode
# ---


@dataclass(frozen=True, slots=True)
class ImportNode:
    """A single Python module represented as a node in the import graph site.

    In theory2.tex §19.1, each Python module corresponds to an object in the
    site category.  The :attr:`coordinate` field embeds the module in the
    Grothendieck site maintained by the jugeo geometry layer, enabling
    presheaf-theoretic operations such as restriction, transport, and descent.

    Trust is explicit: nodes produced by copilot-assisted import analysis enter
    at ``TrustLevel.ORACLE_PROPOSED`` and may be promoted to
    ``RUNTIME_WITNESSED`` once the module has been successfully imported at
    runtime.

    Parameters
    ----------
    module_name:
        The fully-qualified Python module name (e.g. ``"jugeo.geometry.site"``).
    coordinate:
        Semantic coordinate in the jugeo site (theory2.tex §19.1).
    is_package:
        Whether this module is a package (has ``__init__.py`` or is namespace).
    is_namespace:
        Whether this is a namespace package (no ``__init__.py``).
    file_path:
        Absolute path to the module's source file, or ``None`` for built-ins.
    trust:
        Trust level of this node's provenance (theory2.tex §19.1).
    load_time_ms:
        Observed import wall-clock time in milliseconds; ``0.0`` if unknown.
    metadata:
        Arbitrary extra data (copilot annotations, linter results, etc.).
    """

    module_name: str
    coordinate: Coordinate
    is_package: bool
    is_namespace: bool
    file_path: str | None
    trust: TrustLevel
    load_time_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)

    # --- methods ---

    def dotted_path(self) -> str:
        """Return the module's fully-qualified name as a dotted path string.

        This is equivalent to :attr:`module_name` but is provided as a method
        for symmetry with :meth:`as_coordinate` and to allow sub-classes to
        override path rendering.  The copilot display layer uses this method
        when building breadcrumb navigation for a module.

        Returns
        -------
        str
            The dotted module name, e.g. ``"jugeo.python_runtime.import_graph"``.
        """
        return self.module_name

    def parent_package(self) -> str | None:
        """Return the immediate parent package name, or ``None`` for top-level.

        Uses ``rsplit`` on the dotted name so that
        ``"jugeo.python_runtime.import_graph"`` returns
        ``"jugeo.python_runtime"``, and ``"jugeo"`` returns ``None``.

        Returns
        -------
        str | None
            Parent package name, or ``None`` if this is a top-level module.
        """
        if "." not in self.module_name:
            return None
        parent, _ = self.module_name.rsplit(".", 1)
        return parent

    def is_stdlib(self) -> bool:
        """Return ``True`` if this module belongs to the Python standard library.

        Uses :attr:`sys.stdlib_module_names` on Python 3.10+ and falls back to
        a heuristic set of well-known top-level standard library package names
        on earlier interpreters.  The copilot import analysis pipeline uses
        this method to separate first-party, third-party, and stdlib nodes when
        building trust tiers for an import graph.

        Returns
        -------
        bool
            ``True`` when the module is part of CPython's standard library.
        """
        top_level = self.module_name.split(".")[0]
        if sys.version_info >= (3, 10):
            return top_level in sys.stdlib_module_names  # type: ignore[attr-defined]
        # Fallback for Python < 3.10: curated set of stdlib top-level names
        _KNOWN_STDLIB = {
            "abc", "ast", "asyncio", "builtins", "collections", "contextlib",
            "copy", "dataclasses", "enum", "functools", "hashlib", "importlib",
            "inspect", "io", "itertools", "json", "logging", "math", "operator",
            "os", "pathlib", "pickle", "queue", "re", "shutil", "signal",
            "socket", "sqlite3", "string", "struct", "subprocess", "sys",
            "tempfile", "threading", "time", "traceback", "types", "typing",
            "unittest", "urllib", "uuid", "warnings", "weakref", "xml",
        }
        return top_level in _KNOWN_STDLIB

    def as_coordinate(self) -> Coordinate:
        """Return the semantic :class:`Coordinate` for this node.

        Provides a uniform accessor so that algorithms operating on both
        :class:`ImportNode` and :class:`ImportEdge` can retrieve coordinates
        without inspecting field names directly.  The copilot geometry layer
        uses this method when constructing covering families for packages.

        Returns
        -------
        Coordinate
            The jugeo geometry coordinate embedded in this node.
        """
        return self.coordinate

    def to_dict(self) -> dict[str, Any]:
        """Serialise this node to a JSON-safe dictionary.

        The result can be round-tripped through :func:`json.dumps` /
        :func:`json.loads` without loss of information for all primitive
        fields.  Complex objects such as :class:`Coordinate` are rendered as
        their dotted name strings.  Copilot tooling uses this format when
        emitting import-graph snapshots to disk.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of this :class:`ImportNode`.
        """
        coord_name = (
            self.coordinate.name
            if hasattr(self.coordinate, "name")
            else str(self.coordinate)
        )
        return {
            "module_name": self.module_name,
            "coordinate": coord_name,
            "is_package": self.is_package,
            "is_namespace": self.is_namespace,
            "file_path": self.file_path,
            "trust": _trust_value(self.trust),
            "trust_label": self.trust.label() if hasattr(self.trust, "label") else str(self.trust),
            "load_time_ms": self.load_time_ms,
            "metadata": dict(self.metadata),
        }

    def with_trust(self, t: TrustLevel) -> ImportNode:
        """Return a new :class:`ImportNode` with the trust level updated to *t*.

        Uses :func:`dataclasses.replace` to produce an immutable copy,
        preserving all other fields unchanged.  This method is the canonical
        way to promote a copilot-proposed node to a higher trust tier after
        external discharge (theory2.tex §19.1, trust promotion rule).

        Parameters
        ----------
        t:
            The new trust level to apply.

        Returns
        -------
        ImportNode
            A new node identical to ``self`` except for :attr:`trust`.
        """
        return replace(self, trust=t)

    def qualified_name(self) -> str:
        """Return the fully qualified module name with package prefix if applicable.

        For a module inside a package, this is identical to :attr:`module_name`.
        For a top-level module, it is the bare name.  The qualified name is used
        by the copilot import resolution engine when resolving relative imports
        back to absolute paths in the site coordinate system.

        Returns
        -------
        str
            Fully qualified name suitable for use as a dictionary key or
            import statement target.
        """
        if self.is_package and not self.module_name.endswith(".__init__"):
            return self.module_name
        if self.module_name.endswith(".__init__"):
            return self.module_name[: -len(".__init__")]
        return self.module_name


# ---
# §19.1 — ImportEdge
# ---


@dataclass(frozen=True, slots=True)
class ImportEdge:
    """A directed import arrow between two :class:`ImportNode` objects.

    In theory2.tex §19.1, import edges are morphisms in the site category.
    Each edge records the structural kind of the import (absolute, relative,
    star, from-import), the specific names transported, and whether the import
    was resolved dynamically at runtime.

    Copilot-assisted import analysis produces edges at ``ORACLE_PROPOSED``
    trust; static analysis at ``RUNTIME_WITNESSED``; verified solver discharge
    at ``SOLVER_DISCHARGED``.  Trust cannot silently increase — only an explicit
    evidence step may promote an edge.

    Parameters
    ----------
    source:
        The importing module.
    target:
        The module being imported.
    import_kind:
        One of ``"ABSOLUTE"``, ``"RELATIVE"``, ``"STAR"``, or ``"FROM"``.
    imported_names:
        Tuple of names explicitly imported (empty for star and absolute
        imports).
    trust:
        Trust level of this edge's provenance.
    is_dynamic:
        Whether this edge was produced by a ``importlib.import_module`` or
        ``__import__`` call rather than a static ``import`` statement.
    timestamp:
        ISO-8601 timestamp when this edge was recorded.
    """

    source: ImportNode
    target: ImportNode
    import_kind: str
    imported_names: tuple[str, ...]
    trust: TrustLevel
    is_dynamic: bool
    timestamp: str

    # --- methods ---

    def as_morphism(self) -> Morphism:
        """Create a :class:`Morphism` from the source to the target coordinate.

        The morphism kind is chosen based on :attr:`import_kind`:
        ``"FROM"`` → ``RESTRICTION``, ``"STAR"`` → ``INCLUSION``,
        ``"TRANSPORT"`` style re-exports → ``TRANSPORT``.  Copilot uses this
        morphism to compute descent conditions across package boundaries.

        Returns
        -------
        Morphism
            The jugeo geometry morphism representing this import edge.
        """
        _kind_map: dict[str, Any] = {
            "ABSOLUTE": MorphismKind.INCLUSION,
            "RELATIVE": MorphismKind.RESTRICTION,
            "STAR": MorphismKind.INCLUSION,
            "FROM": MorphismKind.RESTRICTION,
        }
        morphism_kind = _kind_map.get(self.import_kind, MorphismKind.RESTRICTION)
        label = f"{self.source.module_name} -> {self.target.module_name}"
        return Morphism(
            source=self.source.coordinate,
            target=self.target.coordinate,
            kind=morphism_kind,
            label=label,
        )

    def is_circular_with(self, other: ImportEdge) -> bool:
        """Return ``True`` if this edge and *other* form a circular import pair.

        A pair is circular when ``self.source == other.target`` and
        ``self.target == other.source``, i.e. module A imports module B and
        module B imports module A.  Copilot static analysis flags such pairs
        as potential :class:`Obstruction` candidates (theory2.tex §19.9).

        Parameters
        ----------
        other:
            The edge to test circularity against.

        Returns
        -------
        bool
            ``True`` when the two edges form a 2-cycle.
        """
        return (
            self.source.module_name == other.target.module_name
            and self.target.module_name == other.source.module_name
        )

    def is_re_export(self) -> bool:
        """Return ``True`` if this edge represents a re-export.

        An edge is a re-export when its kind is ``"FROM"`` and at least one
        name is explicitly imported (i.e. ``len(imported_names) > 0``).
        Copilot marks such edges for special handling in the name-transport
        layer (theory2.tex §19.3).

        Returns
        -------
        bool
            ``True`` for ``FROM`` imports that carry at least one name.
        """
        return self.import_kind == "FROM" and len(self.imported_names) > 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        All nested :class:`ImportNode` objects are serialised via their own
        :meth:`ImportNode.to_dict` methods.  The resulting dict is suitable
        for writing to a JSON import-graph snapshot file used by the copilot
        CI pipeline.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of this :class:`ImportEdge`.
        """
        return {
            "source": self.source.module_name,
            "target": self.target.module_name,
            "import_kind": self.import_kind,
            "imported_names": list(self.imported_names),
            "trust": _trust_value(self.trust),
            "trust_label": self.trust.label() if hasattr(self.trust, "label") else str(self.trust),
            "is_dynamic": self.is_dynamic,
            "timestamp": self.timestamp,
        }

    def invert(self) -> ImportEdge:
        """Return a new :class:`ImportEdge` with source and target swapped.

        The inverted edge carries the same names and trust level but represents
        the reverse morphism.  Copilot uses inverted edges when traversing the
        import graph in reverse to locate which modules depend on a given target.

        Returns
        -------
        ImportEdge
            A new edge with :attr:`source` and :attr:`target` exchanged.
        """
        return replace(self, source=self.target, target=self.source)

    def transport_names(self) -> dict[str, str]:
        """Return a mapping from imported names to their origin module.

        Each name in :attr:`imported_names` is mapped to the fully qualified
        dotted path in the target module.  The copilot name-transport layer
        (theory2.tex §19.3) uses this to build the :class:`ReExportMap` for
        ``__init__.py`` files that re-export public APIs.

        Returns
        -------
        dict[str, str]
            Mapping ``{name: target_module.name}`` for each imported name.
        """
        origin = self.target.module_name
        return {name: f"{origin}.{name}" for name in self.imported_names}


# ---
# §19.2 — PackageFixedPoint
# ---


@dataclass(frozen=True, slots=True)
class PackageFixedPoint:
    """The sheaf-theoretic fixed point of a Python package's import boundary.

    In theory2.tex §19.2, a package fixed point is a pair ``(P, S)`` where
    *P* is the package root coordinate and *S* is the maximal set of modules
    reachable from *P* via internal import edges that does **not** violate
    the closure condition: for every edge in ``internal_edges``, both its
    source and target are members of *S*.

    When ``is_stable`` is ``True`` and ``stability_certificate`` is non-empty,
    the fixed point satisfies the theory's convergence criterion.  Copilot
    package analysis records this certificate when the algorithm terminates.

    Parameters
    ----------
    package_root:
        The package's ``__init__`` module node.
    members:
        Tuple of all module nodes in the package (including the root).
    internal_edges:
        Edges where both source and target are in :attr:`members`.
    external_edges:
        Edges where the target is **not** in :attr:`members`.
    is_stable:
        Whether the fixed-point algorithm has converged.
    stability_certificate:
        Opaque hash certifying stability; empty when ``is_stable`` is
        ``False``.
    computed_at:
        ISO-8601 timestamp when this record was produced.
    """

    package_root: ImportNode
    members: tuple[ImportNode, ...]
    internal_edges: tuple[ImportEdge, ...]
    external_edges: tuple[ImportEdge, ...]
    is_stable: bool
    stability_certificate: str
    computed_at: str

    # --- methods ---

    def covers(self, node: ImportNode) -> bool:
        """Return ``True`` if *node* is a member of this fixed point.

        Membership is determined by :attr:`module_name` equality so that
        nodes with different :class:`Coordinate` instances but the same dotted
        path are still recognised.  The copilot coverage checker uses this
        when verifying that all imports from a package stay within its boundary.

        Parameters
        ----------
        node:
            The node to test.

        Returns
        -------
        bool
            ``True`` when *node*'s module name appears in :attr:`members`.
        """
        member_names = {m.module_name for m in self.members}
        return node.module_name in member_names

    def is_closed_under_imports(self) -> bool:
        """Verify that every target of an internal edge is also a member.

        Closure is the key invariant of theory2.tex §19.2: a fixed point is
        valid only when no internal edge escapes the member set.  Copilot runs
        this check after each iteration of the fixed-point algorithm and stops
        when the invariant holds.

        Returns
        -------
        bool
            ``True`` when all internal edge targets appear in :attr:`members`.
        """
        member_names = {m.module_name for m in self.members}
        for edge in self.internal_edges:
            if edge.target.module_name not in member_names:
                return False
        return True

    def add_member(self, node: ImportNode) -> PackageFixedPoint:
        """Return a new fixed point with *node* added to the member set.

        The stability certificate is cleared and ``is_stable`` is set to
        ``False`` because adding a member may invalidate the current closure.
        Copilot uses this method during the iterative expansion phase of the
        fixed-point computation (theory2.tex §19.2 Algorithm 1).

        Parameters
        ----------
        node:
            The node to add.

        Returns
        -------
        PackageFixedPoint
            A new fixed point with the expanded :attr:`members` tuple.
        """
        if self.covers(node):
            return self
        new_members = self.members + (node,)
        return replace(
            self,
            members=new_members,
            is_stable=False,
            stability_certificate="",
        )

    def verify_stability(self) -> bool:
        """Check that this fixed point satisfies all stability conditions.

        Stability requires:
        1. ``is_stable`` is ``True``.
        2. ``stability_certificate`` is a non-empty string.
        3. The closure invariant holds (all internal edge targets are members).

        Copilot records this boolean in the CI gate report to prevent
        releasing packages whose import graph has not converged.

        Returns
        -------
        bool
            ``True`` when all three stability conditions hold.
        """
        return (
            self.is_stable
            and bool(self.stability_certificate)
            and self.is_closed_under_imports()
        )

    def as_covering_family(self) -> CoveringFamily:
        """Build a :class:`CoveringFamily` from the internal edges of this fixed point.

        The base coordinate is the package root's coordinate.  Each internal
        edge contributes one morphism to the covering family.  The copilot
        descent engine uses this covering family to compute descent data for
        judgments made on the package root (theory2.tex §19.2).

        Returns
        -------
        CoveringFamily
            A covering family whose base is the package root coordinate and
            whose members are the morphisms of all internal edges.
        """
        morphisms = [edge.as_morphism() for edge in self.internal_edges]
        return CoveringFamily(
            base=self.package_root.coordinate,
            members=morphisms,
            label=f"{self.package_root.module_name}-cover",
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        All nested objects are recursively serialised.  This format is used
        by the copilot package analysis tool when writing fixed-point snapshots
        to the project's ``.jugeo/`` cache directory.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of this :class:`PackageFixedPoint`.
        """
        return {
            "package_root": self.package_root.module_name,
            "members": [m.module_name for m in self.members],
            "internal_edges": [e.to_dict() for e in self.internal_edges],
            "external_edges": [e.to_dict() for e in self.external_edges],
            "is_stable": self.is_stable,
            "stability_certificate": self.stability_certificate,
            "computed_at": self.computed_at,
        }

    def internal_site(self) -> Site:
        """Build a :class:`Site` from the internal members and edges.

        Each member node contributes a coordinate, and each internal edge
        contributes a morphism.  The resulting :class:`Site` is the minimal
        sub-site of the global import-graph site that contains all intra-package
        data.  Copilot uses this site when computing local sheaf sections
        (theory2.tex §19.2).

        Returns
        -------
        Site
            A site populated with all internal coordinates and morphisms.
        """
        site = Site(label=f"{self.package_root.module_name}-internal-site")
        for node in self.members:
            site.add_coordinate(node.coordinate)
        for edge in self.internal_edges:
            site.add_morphism(edge.as_morphism())
        return site

    def external_dependencies(self) -> tuple[ImportNode, ...]:
        """Return the unique set of external module targets.

        Deduplicates by :attr:`module_name` so that multiple external edges
        pointing to the same module appear only once.  Copilot uses this to
        generate the package's dependency manifest for the CI trust gate.

        Returns
        -------
        tuple[ImportNode, ...]
            Unique external target nodes in the order they first appear in
            :attr:`external_edges`.
        """
        seen: set[str] = set()
        result: list[ImportNode] = []
        for edge in self.external_edges:
            if edge.target.module_name not in seen:
                seen.add(edge.target.module_name)
                result.append(edge.target)
        return tuple(result)


# ---
# §19.3 — DynamicLoadRecord
# ---


@dataclass(frozen=True, slots=True)
class DynamicLoadRecord:
    """A runtime record of a dynamic module load event.

    In theory2.tex §19.4, dynamic loads are morphisms that cannot be
    discovered by static analysis alone.  Each :class:`DynamicLoadRecord`
    captures a single call to ``importlib.import_module``, ``__import__``, or
    any other dynamic loading mechanism.

    Copilot may propose dynamic load records based on pattern matching in
    source code, but such proposals enter at ``ORACLE_PROPOSED`` trust.  Only
    a witnessed runtime event can raise the trust to ``RUNTIME_WITNESSED``.

    Parameters
    ----------
    load_id:
        Unique identifier for this load event (UUID).
    module_name:
        The name of the module that was loaded.
    loader:
        String identifying the loader, e.g. ``"importlib"`` or
        ``"zipimport"``.
    coordinate:
        Semantic coordinate in the jugeo site, or ``None`` if not yet
        resolved.
    trust:
        Trust level of this load record.
    loaded_at:
        ISO-8601 timestamp of the load event.
    success:
        Whether the import completed without raising an exception.
    error_message:
        Exception message if ``success`` is ``False``, else ``None``.
    """

    load_id: str
    module_name: str
    loader: str
    coordinate: Coordinate | None
    trust: TrustLevel
    loaded_at: str
    success: bool
    error_message: str | None

    # --- methods ---

    def as_import_edge(self, source: ImportNode) -> ImportEdge:
        """Create an :class:`ImportEdge` representing this dynamic load.

        The target :class:`ImportNode` is synthesised from the recorded
        :attr:`module_name` and :attr:`coordinate`.  The edge's trust level
        matches this record's trust.  Copilot uses this method to integrate
        dynamic load records into the static import graph (theory2.tex §19.4).

        Parameters
        ----------
        source:
            The module that issued the dynamic load.

        Returns
        -------
        ImportEdge
            A dynamic import edge from *source* to the loaded module.
        """
        coord = self.coordinate or _make_stub_coordinate(self.module_name)
        target_node = ImportNode(
            module_name=self.module_name,
            coordinate=coord,
            is_package=False,
            is_namespace=False,
            file_path=None,
            trust=self.trust,
            load_time_ms=0.0,
        )
        return ImportEdge(
            source=source,
            target=target_node,
            import_kind="ABSOLUTE",
            imported_names=(),
            trust=self.trust,
            is_dynamic=True,
            timestamp=self.loaded_at,
        )

    def as_obstruction(self) -> Obstruction:
        """Create an :class:`Obstruction` record when this load failed.

        If ``success`` is ``True``, returns a resolved obstruction with no
        violated condition.  Copilot records failed dynamic loads as first-class
        obstructions (theory2.tex §19.4, cohomology class ``H^1_dyn``) so they
        are never silently discarded.

        Returns
        -------
        Obstruction
            An obstruction reflecting the load failure, or a resolved
            trivial obstruction when the load succeeded.
        """
        coord_key = (
            self.coordinate.name
            if self.coordinate and hasattr(self.coordinate, "name")
            else self.module_name
        )
        if self.success:
            return Obstruction(
                obstruction_id=self.load_id,
                violated_condition="",
                coordinate=coord_key,
                is_resolved=True,
                resolution_evidence=self.load_id,
                provenance=(f"dynamic-load:{self.loader}",),
            )
        condition = self.error_message or "unknown dynamic load failure"
        return Obstruction(
            obstruction_id=self.load_id,
            violated_condition=condition,
            coordinate=coord_key,
            repair_hints=(
                f"Verify that {self.module_name!r} is installed.",
                f"Check PYTHONPATH and sys.path for loader={self.loader!r}.",
            ),
            cohomology_class="H^1_dyn",
            is_resolved=False,
            provenance=(f"dynamic-load:{self.loader}", f"loaded-at:{self.loaded_at}"),
        )

    def as_evidence_item(self) -> EvidenceItem:
        """Create an :class:`EvidenceItem` for this dynamic load event.

        Successful loads produce a ``RUNTIME_WITNESS`` evidence item;
        failed loads produce an ``ORACLE_PROPOSAL`` item at the lower trust
        tier.  Copilot integrates these items into the judgment algebra when
        assessing module availability at the call site (theory2.tex §19.4).

        Returns
        -------
        EvidenceItem
            An evidence item encoding the outcome of this dynamic load.
        """
        kind = (
            EvidenceItemKind.RUNTIME_WITNESS
            if self.success
            else EvidenceItemKind.ORACLE_PROPOSAL
        )
        payload: dict[str, Any] = {
            "module_name": self.module_name,
            "loader": self.loader,
            "success": self.success,
            "loaded_at": self.loaded_at,
        }
        if self.error_message:
            payload["error_message"] = self.error_message
        return EvidenceItem(
            kind=kind,
            payload=payload,
            trust_level=self.trust,
            channel="dynamic-loader",
            timestamp=self.loaded_at,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        The :attr:`coordinate` is rendered as its name string to avoid
        serialising the full jugeo geometry object.  Copilot stores these
        dicts in the ``.jugeo/dynamic_loads.json`` cache file.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of this :class:`DynamicLoadRecord`.
        """
        coord_repr = (
            self.coordinate.name
            if self.coordinate and hasattr(self.coordinate, "name")
            else None
        )
        return {
            "load_id": self.load_id,
            "module_name": self.module_name,
            "loader": self.loader,
            "coordinate": coord_repr,
            "trust": _trust_value(self.trust),
            "trust_label": self.trust.label() if hasattr(self.trust, "label") else str(self.trust),
            "loaded_at": self.loaded_at,
            "success": self.success,
            "error_message": self.error_message,
        }

    def is_trusted(self) -> bool:
        """Return ``True`` when this record's trust is at or above ``ORACLE_PROPOSED``.

        ``ORACLE_PROPOSED`` is the minimum tier at which copilot-assisted load
        records are considered actionable.  Records at ``UNVERIFIED`` or
        ``CONTRADICTED`` should not be used to build import edges without
        additional evidence (theory2.tex §19.4, trust gate condition).

        Returns
        -------
        bool
            ``True`` when :attr:`trust` has a numeric value ≥ 2 (``ORACLE_PROPOSED``).
        """
        return _trust_value(self.trust) >= 2

    def retry_hint(self) -> str:
        """Return a human-readable hint for retrying a failed dynamic load.

        If the load succeeded, returns an empty string.  Otherwise builds a
        helpful message incorporating the loader name, module name, and error
        message.  Copilot surfaces this hint in the IDE quick-fix panel when
        an unresolved dynamic import is detected.

        Returns
        -------
        str
            A suggestion string, or empty string if the load succeeded.
        """
        if self.success:
            return ""
        base = f"Retry loading {self.module_name!r} via {self.loader!r}."
        if self.error_message:
            if "No module named" in self.error_message:
                return (
                    f"{base}  Ensure the package is installed: "
                    f"``pip install {self.module_name.split('.')[0]}``."
                )
            if "cannot import name" in self.error_message:
                return (
                    f"{base}  The attribute may have been renamed or removed. "
                    f"Check the {self.module_name!r} changelog."
                )
        return f"{base}  See error: {self.error_message!r}."


# ---
# §19.4 — ReExportMap
# ---


@dataclass(frozen=True, slots=True)
class ReExportMap:
    """A typed record of how one module re-exports names from another.

    In theory2.tex §19.3, re-exports are transport morphisms: a name defined
    in module *A* is made available under the same or an aliased name in
    module *B*'s public namespace.  The :class:`ReExportMap` captures this
    transport together with trust and public-API information.

    Copilot analyses ``__init__.py`` files and ``__all__`` declarations to
    produce :class:`ReExportMap` records.  These enter at ``ORACLE_PROPOSED``
    trust and may be promoted once verified against the runtime
    ``sys.modules`` state.

    Parameters
    ----------
    source_module:
        The module where the names are originally defined.
    target_module:
        The module that re-exports the names.
    exported_names:
        Tuple of names transported from source to target.
    aliased_names:
        Mapping ``{original_name: alias}`` for names that are re-exported
        under a different identifier.
    trust:
        Trust level of this re-export map.
    is_public:
        Whether the re-exported names are part of the public API
        (i.e. would appear in a generated ``__all__``).
    star_export:
        Whether this re-export was produced by a ``from X import *`` statement.
    """

    source_module: ImportNode
    target_module: ImportNode
    exported_names: tuple[str, ...]
    aliased_names: dict[str, str]
    trust: TrustLevel
    is_public: bool
    star_export: bool

    # --- methods ---

    def as_morphism(self) -> Morphism:
        """Create a transport :class:`Morphism` for this re-export map.

        The morphism kind is ``TRANSPORT`` to reflect that names are being
        moved from source to target (theory2.tex §19.3, name transport
        functor).  Copilot uses this morphism when constructing the presheaf
        of module namespaces over the import-graph site.

        Returns
        -------
        Morphism
            A ``TRANSPORT`` morphism from the source to the target coordinate.
        """
        label = (
            f"re-export:{self.source_module.module_name}"
            f"->{self.target_module.module_name}"
        )
        return Morphism(
            source=self.source_module.coordinate,
            target=self.target_module.coordinate,
            kind=MorphismKind.TRANSPORT,
            label=label,
        )

    def resolve_name(self, name: str) -> str:
        """Resolve an alias for *name*, returning the original if no alias exists.

        If *name* has an entry in :attr:`aliased_names`, returns the alias.
        Otherwise returns *name* unchanged.  Copilot uses this when walking
        the re-export chain to find the canonical definition site of a symbol
        (theory2.tex §19.3, canonical name resolution).

        Parameters
        ----------
        name:
            The name to resolve.

        Returns
        -------
        str
            The resolved (aliased) name, or *name* if no alias is set.
        """
        return self.aliased_names.get(name, name)

    def filter_public(self) -> ReExportMap:
        """Return a new :class:`ReExportMap` containing only public names.

        Public names are those that do not begin with an underscore ``_``.
        Copilot uses this when generating the public API surface documentation
        for a package (theory2.tex §19.3, public API sheaf).

        Returns
        -------
        ReExportMap
            A new map with private names removed from :attr:`exported_names`
            and :attr:`aliased_names`.
        """
        public_exported = tuple(n for n in self.exported_names if not n.startswith("_"))
        public_aliased = {
            k: v
            for k, v in self.aliased_names.items()
            if not k.startswith("_") and not v.startswith("_")
        }
        return replace(
            self,
            exported_names=public_exported,
            aliased_names=public_aliased,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Nested :class:`ImportNode` objects are rendered as their
        :attr:`module_name` strings.  Copilot writes these dicts to the
        ``.jugeo/reexport_maps.json`` file during package manifest generation.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of this :class:`ReExportMap`.
        """
        return {
            "source_module": self.source_module.module_name,
            "target_module": self.target_module.module_name,
            "exported_names": list(self.exported_names),
            "aliased_names": dict(self.aliased_names),
            "trust": _trust_value(self.trust),
            "trust_label": self.trust.label() if hasattr(self.trust, "label") else str(self.trust),
            "is_public": self.is_public,
            "star_export": self.star_export,
        }

    def compose_with(self, other: ReExportMap) -> ReExportMap:
        """Compose two re-export maps to form a transitive transport chain.

        ``self.compose_with(other)`` produces a new map from
        ``self.source_module`` to ``other.target_module`` that transports
        the intersection of both maps' exported names.  Alias resolution
        is threaded through both maps.  Copilot uses this to collapse chains
        of ``__init__.py`` re-exports into a single top-level map
        (theory2.tex §19.3, associativity Lemma 19.3.2).

        Parameters
        ----------
        other:
            The map to compose after ``self``.

        Returns
        -------
        ReExportMap
            Composed re-export map from ``self.source_module`` to
            ``other.target_module``.

        Raises
        ------
        ValueError
            If ``self.target_module`` does not match ``other.source_module``.
        """
        if self.target_module.module_name != other.source_module.module_name:
            raise ValueError(
                f"Cannot compose: self.target={self.target_module.module_name!r} "
                f"!= other.source={other.source_module.module_name!r}"
            )
        # Names must appear in both maps to survive composition
        common_names = tuple(
            n for n in self.exported_names if n in other.exported_names
        )
        # Thread aliases: first resolve through self, then through other
        composed_aliases: dict[str, str] = {}
        for name in common_names:
            intermediate = self.aliased_names.get(name, name)
            final = other.aliased_names.get(intermediate, intermediate)
            if final != name:
                composed_aliases[name] = final
        # Trust is the minimum of the two maps (monotonicity)
        composed_trust = (
            self.trust
            if _trust_value(self.trust) <= _trust_value(other.trust)
            else other.trust
        )
        return ReExportMap(
            source_module=self.source_module,
            target_module=other.target_module,
            exported_names=common_names,
            aliased_names=composed_aliases,
            trust=composed_trust,
            is_public=self.is_public and other.is_public,
            star_export=self.star_export or other.star_export,
        )

    def inverse_map(self) -> dict[str, str]:
        """Invert the :attr:`aliased_names` mapping.

        Returns a new dict mapping alias → original name so callers can
        look up where an aliased export originated.  Copilot uses this when
        tracing a symbol in the public API back to its canonical definition
        in the source module (theory2.tex §19.3, inverse name resolution).

        Returns
        -------
        dict[str, str]
            Mapping ``{alias: original_name}`` for all aliased entries.
        """
        return {alias: original for original, alias in self.aliased_names.items()}

    def trust_transported(self) -> TrustLevel:
        """Return the minimum trust level between source and target modules.

        Trust is not elevated by transport: a re-export from a low-trust
        module into a high-trust module must carry the lower trust.  Copilot
        enforces this invariant so that copilot-proposed re-exports cannot
        implicitly elevate trust when crossing module boundaries
        (theory2.tex §19.3, trust monotonicity Lemma 19.3.1).

        Returns
        -------
        TrustLevel
            The lower of :attr:`source_module.trust` and
            :attr:`target_module.trust`.
        """
        src_val = _trust_value(self.source_module.trust)
        tgt_val = _trust_value(self.target_module.trust)
        if src_val <= tgt_val:
            return self.source_module.trust
        return self.target_module.trust


# ---
# Module-level convenience: stable hash for node identity
# ---

def _node_identity_hash(node: ImportNode) -> str:
    """Compute a deterministic content-addressed hash for an :class:`ImportNode`.

    Uses the module name, file path, and trust value so that two independently
    constructed nodes for the same module compare equal under this hash.
    Copilot deduplication logic relies on this hash to merge redundant nodes
    from multiple analysis passes.

    Parameters
    ----------
    node:
        The node to hash.

    Returns
    -------
    str
        A hex SHA-256 digest identifying this node's content.
    """
    payload = json.dumps(
        {
            "module_name": node.module_name,
            "file_path": node.file_path,
            "trust": _trust_value(node.trust),
        },
        sort_keys=True,
    )
    return _stable_hash(payload)


def _edge_identity_hash(edge: ImportEdge) -> str:
    """Compute a deterministic content-addressed hash for an :class:`ImportEdge`.

    Uses source/target module names, import kind, and imported names so that
    two edges representing the same static import statement compare equal.
    Copilot uses this to deduplicate edges when merging incremental import
    graph analyses.

    Parameters
    ----------
    edge:
        The edge to hash.

    Returns
    -------
    str
        A hex SHA-256 digest identifying this edge's content.
    """
    payload = json.dumps(
        {
            "source": edge.source.module_name,
            "target": edge.target.module_name,
            "import_kind": edge.import_kind,
            "imported_names": sorted(edge.imported_names),
        },
        sort_keys=True,
    )
    return _stable_hash(payload)


def _now_iso_local() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Wraps ``time.strftime`` so callers do not need to import ``time`` directly.
    Used by factory functions and builder utilities in sibling modules of
    the ``jugeo.python_runtime.import_graph`` package.

    Returns
    -------
    str
        UTC timestamp in ``YYYY-MM-DDTHH:MM:SSZ`` format.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _make_empty_fixed_point(root: ImportNode) -> PackageFixedPoint:
    """Construct an empty (unstable) :class:`PackageFixedPoint` for *root*.

    Returns a fixed point containing only the root node, with no edges and
    ``is_stable=False``.  Copilot uses this as the seed for the iterative
    fixed-point expansion algorithm (theory2.tex §19.2 Algorithm 1).

    Parameters
    ----------
    root:
        The package root node.

    Returns
    -------
    PackageFixedPoint
        A seed fixed point containing only *root*.
    """
    return PackageFixedPoint(
        package_root=root,
        members=(root,),
        internal_edges=(),
        external_edges=(),
        is_stable=False,
        stability_certificate="",
        computed_at=_now_iso_local(),
    )


def _certify_fixed_point(fp: PackageFixedPoint) -> PackageFixedPoint:
    """Compute a stability certificate for a converged :class:`PackageFixedPoint`.

    Hashes the sorted member names and internal edge sources/targets to produce
    a deterministic certificate string.  Uses :func:`dataclasses.replace` so
    the original object is not mutated.  Copilot CI stores this certificate in
    the project manifest to detect regressions across commits (theory2.tex
    §19.2, stability certificate definition).

    Parameters
    ----------
    fp:
        A fixed point whose closure invariant has already been verified.

    Returns
    -------
    PackageFixedPoint
        A new fixed point with ``is_stable=True`` and a non-empty
        :attr:`stability_certificate`.
    """
    member_names = sorted(m.module_name for m in fp.members)
    edge_keys = sorted(
        f"{e.source.module_name}->{e.target.module_name}"
        for e in fp.internal_edges
    )
    raw = json.dumps({"members": member_names, "edges": edge_keys}, sort_keys=True)
    cert = _stable_hash(raw)
    return replace(fp, is_stable=True, stability_certificate=cert)
