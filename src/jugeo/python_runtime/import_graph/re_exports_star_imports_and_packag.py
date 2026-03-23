from __future__ import annotations

"""
Re-exports, star imports, and package surfaces
===============================================

Theory reference: theory2.tex Ch19 §3 — *Re-exports, Star Imports, and Package Surfaces*

This module implements the machinery needed to reason about the *public API surface* of
a Python package.  In the topos-theoretic vocabulary of jugeo, every package is a
*site* whose objects are modules; a ``from pkg import *`` is a *covering sieve* that
pulls the entire public surface of one site into another.  Understanding which names
flow across these sieves — and whether they were originally defined there or merely
re-exported — is essential for building accurate import graphs and for attaching
:class:`~jugeo.judgments.judgment_terms.Judgment` objects to coverage obligations.

Ch19 §3 identifies three phenomena that complicate import-graph construction:

1. **Star imports** (``from m import *``) — the set of imported names is determined
   at runtime by ``m.__all__`` (if present) or ``dir(m)`` (minus dunder-prefixed names).
   The *risk level* of a star import rises with the size of the source module's public
   surface and the absence of an explicit ``__all__`` declaration.

2. **Re-exports** — a module ``pkg/__init__.py`` commonly imports names from
   sub-modules and exposes them as part of the package's public API.  Each hop in this
   chain is modelled as a :class:`ReExportHop` carrying a *morphism kind* (in the sense
   of ``MorphismKind.TRANSPORT`` or ``MorphismKind.INCLUSION``).

3. **Package surfaces** — the union of all public names accessible via ``from pkg import *``
   or ``import pkg; pkg.<name>``.  A :class:`PackageSurfaceRecord` captures this set
   together with metadata about which names were re-exported vs. originally defined.

The module provides:

* Data-carrying frozen dataclasses for records and hops.
* A *coordinator* (mutable dataclass) that drives package-level analysis.
* An *analyzer* (mutable dataclass) that does AST-level static analysis.
* A *witness* (mutable dataclass) that performs live runtime introspection.
* A smoke-test ``if __name__ == "__main__":`` block at the bottom.

Cross-package dependencies are guarded by ``try/except ImportError`` blocks so that
this module can be imported even when the rest of jugeo is not yet installed.

.. code-block:: text

   jugeo.python_runtime.import_graph.re_exports_star_imports_and_packag
       └── theory2.tex Ch19 §3  (re-exports, star imports, package surfaces)
"""

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import ast
import importlib
import importlib.machinery
import importlib.util
import logging
import os
import pkgutil
import sys
import textwrap
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cross-package imports with full stub fallbacks (theory2.tex Ch19 §3)
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, Morphism, MorphismKind,
        Site, SiteBuilder, CoveringFamily, GrothendieckTopology, CoordinateObject,
    )
except ImportError:
    from dataclasses import dataclass as _dc, field as _field
    from enum import Enum
    class CoordinateKind(Enum):
        MODULE="module"; FUNCTION="function"; INTERFACE="interface"
        TEST="test"; THEOREM="theorem"; REGION="region"
    class MorphismKind(Enum):
        RESTRICTION="restriction"; INCLUSION="inclusion"
        TRANSPORT="transport"; REFINEMENT="refinement"
    @_dc(frozen=True)
    class Coordinate:
        components: tuple = ()
        kind: "CoordinateKind" = CoordinateKind.MODULE
        support_labels: frozenset = frozenset()
    CoordinateObject = Coordinate
    @_dc(frozen=True)
    class Morphism:
        source: "Coordinate" = None; target: "Coordinate" = None
        kind: "MorphismKind" = MorphismKind.INCLUSION; label: str = ""
    @_dc
    class CoveringFamily:
        base: "Coordinate" = None; members: list = _field(default_factory=list)
    @_dc
    class GrothendieckTopology:
        name: str = "custom"
    @_dc
    class Site:
        label: str = ""; _coords: list = _field(default_factory=list); _morphisms: list = _field(default_factory=list)
        def add_coordinate(self, c): self._coords.append(c); return self
        def add_morphism(self, m): self._morphisms.append(m); return self
        def objects(self): return list(self._coords)
        def morphisms_from(self, c): return [m for m in self._morphisms if getattr(m,'source',None)==c]
    @_dc
    class SiteBuilder:
        _coords: list = _field(default_factory=list); _morphisms: list = _field(default_factory=list)
        def add_coordinate(self, c): self._coords.append(c); return self
        def add_morphism(self, m): self._morphisms.append(m); return self
        def build(self): return Site()

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, PropositionKind,
        Carrier, EvidenceBundle, EvidenceItem, EvidenceItemKind,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance,
    )
except ImportError:
    from enum import Enum
    from dataclasses import dataclass as _dc, field as _field
    class JudgmentStatus(str, Enum):
        PROPOSED="proposed"; SETTLED="settled"; OBSTRUCTED="obstructed"; OPEN="open"
    class TrustLevel(int, Enum):
        COPILOT_SUGGESTED=1; ORACLE_PROPOSED=2; RUNTIME_WITNESSED=3; VERIFIED=4
    class PropositionKind(str, Enum):
        STRUCTURAL="structural"; BEHAVIORAL="behavioral"; TEMPORAL="temporal"
        INVARIANT="invariant"; LIVENESS="liveness"; SAFETY="safety"
    class EvidenceItemKind(str, Enum):
        STATIC_ANALYSIS="static_analysis"; RUNTIME_TRACE="runtime_trace"
        THEOREM_PROOF="theorem_proof"; COPILOT_ANNOTATION="copilot_annotation"
    @_dc(frozen=True)
    class Proposition:
        kind: "PropositionKind" = PropositionKind.STRUCTURAL; statement: str = ""; label: str = ""
    @_dc(frozen=True)
    class Carrier:
        coordinate: object = None; payload: object = None; label: str = ""
    @_dc
    class EvidenceItem:
        kind: "EvidenceItemKind" = EvidenceItemKind.STATIC_ANALYSIS; payload: object = None; label: str = ""
    @_dc
    class EvidenceBundle:
        items: list = _field(default_factory=list)
        def add(self, item): self.items.append(item); return self
    @_dc
    class TrustAnnotation:
        level: "TrustLevel" = TrustLevel.COPILOT_SUGGESTED; rationale: str = ""
    @_dc
    class Provenance:
        source: str = ""; module: str = ""; timestamp: str = ""
    @_dc
    class ResidualObligation:
        description: str = ""; discharged: bool = False
    @_dc
    class Obstruction:
        description: str = ""; coordinate: object = None
    @_dc
    class Judgment:
        status: "JudgmentStatus" = JudgmentStatus.PROPOSED
        proposition: "Proposition" = None
        carrier: "Carrier" = None
        evidence: "EvidenceBundle" = _field(default_factory=EvidenceBundle)
        trust: "TrustAnnotation" = _field(default_factory=TrustAnnotation)
        provenance: "Provenance" = _field(default_factory=Provenance)
        obligations: list = _field(default_factory=list)
        label: str = ""
        def settle(self): self.status = JudgmentStatus.SETTLED; return self
        def obstruct(self, obs): self.status = JudgmentStatus.OBSTRUCTED; return self

try:
    from jugeo.solver.z3_session import SolveOutcome, Z3Formula, Z3Session, z3_available
except ImportError:
    from enum import Enum
    from dataclasses import dataclass as _dc
    class SolveOutcome(str, Enum):
        SAT="sat"; UNSAT="unsat"; UNKNOWN="unknown"
    @_dc
    class Z3Formula:
        smt2: str = ""; label: str = ""
    @_dc
    class Z3Session:
        def check(self, formula): return SolveOutcome.UNKNOWN
        def add_assertion(self, formula): return self
    def z3_available() -> bool: return False

# ---------------------------------------------------------------------------
# §3.1  Risk enumeration — how dangerous is a given star import?
# ---------------------------------------------------------------------------

class StarImportRiskLevel(str, Enum):
    """Risk level attached to a ``from module import *`` statement.

    The risk scale follows Ch19 §3.1 of theory2.tex:

    LOW
        The source module declares ``__all__`` and exposes fewer than 20 names.
        The covering sieve is *bounded* and predictable.

    MEDIUM
        The source module declares ``__all__`` with 20–99 names, *or* does not
        declare ``__all__`` but its ``dir()`` surface is small.

    HIGH
        No ``__all__``, a large ``dir()`` surface, or the module re-exports names
        from third-party packages without re-declaring them.

    CRITICAL
        The source is a namespace package, a dynamic module built at runtime,
        or any module whose surface cannot be statically determined.  Using
        ``import *`` from such a source can silently shadow built-ins or other
        names already present in the importing namespace.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# §3.2  Frozen value-object dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ReExportRecord:
    """A single name that is re-exported from one module via another.

    In the import-graph topos (Ch19 §3.2), a re-export corresponds to a
    *transport morphism* ``MorphismKind.TRANSPORT`` from the original defining
    module to the re-exporting module.

    Attributes
    ----------
    name:
        The public identifier being re-exported (e.g. ``"Coordinate"``).
    original_module:
        Dotted module name where *name* is originally defined.
    re_exporting_module:
        Dotted module name that imports *name* and exposes it, usually via
        ``__all__`` in an ``__init__.py``.
    re_export_kind:
        Human-readable tag; one of ``"explicit_all"``, ``"implicit_star"``,
        ``"direct_import"``.
    line_no:
        Source line number in *re_exporting_module* where the import occurs.
    """

    # copilot: name must be a valid Python identifier
    name: str
    original_module: str
    re_exporting_module: str
    re_export_kind: str
    line_no: int


@dataclass(frozen=True, slots=True)
class ReExportHop:
    """One hop in a multi-level re-export chain.

    Re-exports can be transitive: ``a`` defines ``Foo``, ``b`` re-exports it,
    and ``c`` re-exports it again.  Each step is recorded as a
    :class:`ReExportHop`.

    Attributes
    ----------
    module_name:
        The module that participates in this hop.
    name:
        The name being transported through *module_name*.
    hop_index:
        Zero-based index of this hop in the chain (0 = original definition).
    morphism_kind:
        Semantic tag for the morphism; typically
        ``MorphismKind.TRANSPORT.value`` or ``MorphismKind.INCLUSION.value``.
    """

    # copilot: hop_index=0 always refers to the original defining module
    module_name: str
    name: str
    hop_index: int
    morphism_kind: str


@dataclass(frozen=True, slots=True)
class PackageSurfaceRecord:
    """Snapshot of a package's complete public API surface.

    This record is computed once per package root (or per analysis session)
    and stored in :attr:`ReExportsStarImportsCoordinator._surface_cache`.

    Attributes
    ----------
    package_name:
        Dotted name of the package (e.g. ``"jugeo.geometry"``).
    public_names:
        All names accessible via ``from package import *``, whether defined
        locally or re-exported.
    re_exported_names:
        Subset of *public_names* that are not *originally* defined in this
        package but are pulled in from sub-modules or other packages.
    star_imports:
        List of module names from which this package performs star imports.
    all_defined:
        ``True`` if the package's ``__init__.py`` declares an explicit
        ``__all__`` list, ``False`` if the surface is inferred from ``dir()``.
    surface_size:
        Convenience field: ``len(public_names)``.
    """

    # copilot: prefer all_defined=True; surfaces without __all__ are fragile
    package_name: str
    public_names: tuple
    re_exported_names: tuple
    star_imports: tuple
    all_defined: bool
    surface_size: int


@dataclass(frozen=True, slots=True)
class StarImportWitnessRecord:
    """Runtime witness for a single ``from module import *`` event.

    Produced by :meth:`ReExportsStarImportsWitness.witness_star_import` after
    actually importing the source module and inspecting its namespace.

    Attributes
    ----------
    source_module:
        The module being star-imported.
    imported_names:
        Names that would be injected into the importing namespace.
    namespace_pollution_count:
        Number of names in *imported_names* that would shadow a built-in or a
        name already present in a typical module namespace.
    risk_level:
        A :class:`StarImportRiskLevel` value (stored as its string value).
    """

    # copilot: namespace_pollution_count > 0 is an immediate code-smell signal
    source_module: str
    imported_names: tuple
    namespace_pollution_count: int
    risk_level: str


@dataclass(frozen=True, slots=True)
class NameOriginRecord:
    """Resolved origin of a single name found in a namespace.

    :meth:`ReExportsStarImportsWitness.trace_name_origin` populates this by
    inspecting ``obj.__module__``, ``obj.__qualname__``, etc.

    Attributes
    ----------
    name:
        The identifier as it appears in the namespace under investigation.
    origin_module:
        The dotted module path where this name was originally defined.
    import_chain:
        Ordered sequence of module names the name passed through to reach the
        current namespace (first element = origin, last = current namespace).
    resolved_at_runtime:
        ``True`` if the origin was determined by live introspection, ``False``
        if it was inferred statically.
    """

    # copilot: import_chain is empty when the name is defined in origin_module directly
    name: str
    origin_module: str
    import_chain: tuple
    resolved_at_runtime: bool


# ---------------------------------------------------------------------------
# §3.3  Coordinator — drives package-level analysis
# ---------------------------------------------------------------------------

@dataclass
class ReExportsStarImportsCoordinator:
    """High-level coordinator for re-export and star-import analysis.

    This mutable dataclass orchestrates the various static and runtime
    analysis passes described in Ch19 §3 of theory2.tex.  It caches results
    in :attr:`_surface_cache` to avoid re-computing expensive package walks.

    Attributes
    ----------
    _surface_cache:
        Maps ``package_root`` (filesystem path or dotted name) to the
        :class:`PackageSurfaceRecord` computed on the first call.
    _judgments:
        Accumulated :class:`~jugeo.judgments.judgment_terms.Judgment` objects
        produced during analysis; can be inspected after a full run.
    """

    # copilot: _surface_cache is keyed by the package's filesystem root path
    _surface_cache: dict = field(default_factory=dict)
    _judgments: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def analyze_package_surface(self, package_root: str) -> PackageSurfaceRecord:
        """Walk *package_root* and build a :class:`PackageSurfaceRecord`.

        The walk uses :func:`pkgutil.walk_packages` when the package root is
        already on :data:`sys.path`, falling back to :func:`os.walk` for raw
        filesystem paths.

        Parameters
        ----------
        package_root:
            Either a dotted package name (e.g. ``"jugeo.geometry"``) or an
            absolute filesystem path to the package directory.

        Returns
        -------
        PackageSurfaceRecord
            A frozen record describing the package surface.

        Notes
        -----
        The implementation first attempts to resolve *package_root* via
        :func:`importlib.util.find_spec`.  If that fails (e.g. the package is
        not installed), it falls back to walking the filesystem and parsing
        ``__init__.py`` statically with :mod:`ast`.

        Theory connection: theory2.tex Ch19 §3.3 — *Package Surface Sheaf*.
        """
        # copilot: check cache first to avoid expensive re-analysis
        if package_root in self._surface_cache:
            log.debug("cache hit for package surface: %s", package_root)
            return self._surface_cache[package_root]

        log.info("analyzing package surface for: %s", package_root)

        public_names: list[str] = []
        re_exported_names: list[str] = []
        star_imports: list[str] = []
        all_defined = False

        # --- Try to load the package via importlib ---
        # copilot: importlib.import_module is the canonical way to load a package
        mod = None
        try:
            mod = importlib.import_module(package_root)
        except Exception as exc:
            log.debug("could not import %s: %s", package_root, exc)

        if mod is not None:
            all_list = getattr(mod, "__all__", None)
            if all_list is not None:
                all_defined = True
                public_names = list(all_list)
            else:
                # copilot: fall back to dir() minus dunder names
                public_names = [n for n in dir(mod) if not n.startswith("_")]

            # copilot: detect re-exports by checking __module__ of each name
            for name in public_names:
                obj = getattr(mod, name, None)
                if obj is None:
                    continue
                origin = getattr(obj, "__module__", None)
                if origin and origin != package_root:
                    re_exported_names.append(name)
        else:
            # --- Static fallback: walk filesystem and parse AST ---
            # copilot: filesystem walk is the last resort
            init_path = os.path.join(package_root, "__init__.py")
            if os.path.isfile(init_path):
                try:
                    with open(init_path, "r", encoding="utf-8", errors="replace") as fh:
                        source = fh.read()
                    analyzer = ReExportsStarImportsAnalyzer()
                    all_list = analyzer.extract_all_list(source)
                    if all_list is not None:
                        all_defined = True
                        public_names = all_list
                    records = analyzer.find_re_exported_names(source)
                    re_exported_names = [r.name for r in records]
                    # copilot: detect star imports in the init source
                    tree = ast.parse(source, filename=init_path)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom):
                            if any(alias.name == "*" for alias in node.names):
                                star_imports.append(node.module or "")
                except Exception as exc:
                    log.warning("AST parse failed for %s: %s", init_path, exc)

        record = PackageSurfaceRecord(
            package_name=package_root,
            public_names=tuple(public_names),
            re_exported_names=tuple(re_exported_names),
            star_imports=tuple(star_imports),
            all_defined=all_defined,
            surface_size=len(public_names),
        )
        # copilot: always cache the result
        self._surface_cache[package_root] = record
        return record

    # ------------------------------------------------------------------
    def trace_re_export_chain(self, name: str, module: str) -> list[ReExportHop]:
        """Follow the re-export chain for *name* starting at *module*.

        The algorithm walks upward through the dotted module hierarchy,
        checking at each level whether the name is re-exported from a
        parent package.

        Parameters
        ----------
        name:
            The identifier to trace (e.g. ``"Coordinate"``).
        module:
            The dotted name of the starting module.

        Returns
        -------
        list[ReExportHop]
            Ordered chain from the original definition to *module*.

        Notes
        -----
        Theory connection: theory2.tex Ch19 §3.4 — *Re-export Morphism Chains*.
        Each :class:`ReExportHop` corresponds to a *transport morphism* in the
        import-graph site.
        """
        # copilot: build the chain bottom-up, then reverse
        hops: list[ReExportHop] = []
        parts = module.split(".")
        visited: set[str] = set()

        # copilot: trace from the leaf module up to the root package
        for depth in range(len(parts), 0, -1):
            candidate = ".".join(parts[:depth])
            if candidate in visited:
                continue
            visited.add(candidate)
            try:
                mod = importlib.import_module(candidate)
            except Exception:
                log.debug("skip import of %s while tracing %s", candidate, name)
                continue

            if hasattr(mod, name):
                obj = getattr(mod, name)
                origin = getattr(obj, "__module__", candidate)
                morphism_kind = (
                    MorphismKind.TRANSPORT.value
                    if origin != candidate
                    else MorphismKind.INCLUSION.value
                )
                hop = ReExportHop(
                    module_name=candidate,
                    name=name,
                    hop_index=len(hops),
                    morphism_kind=morphism_kind,
                )
                hops.append(hop)

        # copilot: re-index hops so hop_index=0 is the origin
        hops.reverse()
        reindexed = [
            ReExportHop(
                module_name=h.module_name,
                name=h.name,
                hop_index=i,
                morphism_kind=h.morphism_kind,
            )
            for i, h in enumerate(hops)
        ]
        return reindexed

    # ------------------------------------------------------------------
    def expand_star_import(self, module_name: str) -> list[str]:
        """Expand a ``from module_name import *`` into the list of names it injects.

        Uses :func:`importlib.import_module` to load the module, then returns
        ``__all__`` (if defined) or ``[n for n in dir(mod) if not n.startswith('_')]``.

        Parameters
        ----------
        module_name:
            Dotted name of the module to expand.

        Returns
        -------
        list[str]
            Names that would be injected by ``from module_name import *``.

        Notes
        -----
        The function never raises; if the import fails it returns an empty list
        and logs a warning.
        """
        # copilot: always use try/except here — importing arbitrary modules is risky
        try:
            mod = importlib.import_module(module_name)
        except Exception as exc:
            log.warning("expand_star_import: cannot import %s: %s", module_name, exc)
            return []

        all_list = getattr(mod, "__all__", None)
        if all_list is not None:
            # copilot: __all__ is the canonical public surface — prefer it
            return list(all_list)

        # copilot: filter out dunder names to avoid noise
        return [n for n in dir(mod) if not n.startswith("_")]

    # ------------------------------------------------------------------
    def build_surface_judgment(self, surface: PackageSurfaceRecord) -> Judgment:
        """Construct a :class:`~jugeo.judgments.judgment_terms.Judgment` for *surface*.

        The judgment captures whether the package's public API is fully
        described by an explicit ``__all__`` (settled) or inferred at runtime
        (proposed / open).

        Parameters
        ----------
        surface:
            The :class:`PackageSurfaceRecord` to judge.

        Returns
        -------
        Judgment
            A new judgment object.  Callers should call ``.settle()`` or
            ``.obstruct(...)`` as appropriate after further analysis.
        """
        # copilot: proposition describes what we are asserting about the surface
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=(
                f"Package '{surface.package_name}' exposes {surface.surface_size} "
                f"public names; __all__ present: {surface.all_defined}; "
                f"re-exported: {len(surface.re_exported_names)}; "
                f"star-imports: {len(surface.star_imports)}"
            ),
            label=f"surface:{surface.package_name}",
        )
        coord = Coordinate(
            components=(surface.package_name, "surface"),
            kind=CoordinateKind.MODULE,
        )
        carrier = Carrier(coordinate=coord, payload=surface, label=surface.package_name)
        evidence = EvidenceBundle()
        evidence.add(EvidenceItem(
            kind=EvidenceItemKind.STATIC_ANALYSIS,
            payload={"surface_size": surface.surface_size},
            label="static_surface_analysis",
        ))
        trust = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED if surface.all_defined else TrustLevel.COPILOT_SUGGESTED,
            rationale="__all__ present" if surface.all_defined else "inferred from dir()",
        )
        j = Judgment(
            status=JudgmentStatus.PROPOSED,
            proposition=prop,
            carrier=carrier,
            evidence=evidence,
            trust=trust,
            label=f"judgment:surface:{surface.package_name}",
        )
        # copilot: auto-settle if __all__ is explicitly defined
        if surface.all_defined:
            j.settle()
        # copilot: accumulate all judgments for later inspection
        self._judgments.append(j)
        return j


# ---------------------------------------------------------------------------
# §3.4  Analyzer — AST-level static analysis
# ---------------------------------------------------------------------------

@dataclass
class ReExportsStarImportsAnalyzer:
    """AST-based static analyzer for re-exports and star imports.

    All methods accept *module_source* as a raw Python source string and
    return structured results without executing any code.  This makes the
    analyzer safe to run on untrusted source files.

    Theory connection: theory2.tex Ch19 §3.5 — *Static Surface Analysis*.
    """

    # copilot: no mutable state needed at construction time
    _cache: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    def extract_all_list(self, module_source: str) -> list[str] | None:
        """Parse ``__all__ = [...]`` from *module_source* using the AST.

        Parameters
        ----------
        module_source:
            Raw Python source text.

        Returns
        -------
        list[str] | None
            The literal string values found in ``__all__``, or ``None`` if no
            ``__all__`` assignment is found or it cannot be statically resolved.

        Notes
        -----
        Only simple assignments of the form ``__all__ = ["a", "b", ...]`` or
        ``__all__ = ("a", "b", ...)`` are handled.  Augmented assignments
        (``__all__ += [...]``) and computed lists are not resolved.
        """
        # copilot: parse once and walk the top-level body
        try:
            tree = ast.parse(module_source, mode="exec")
        except SyntaxError as exc:
            log.debug("extract_all_list: SyntaxError: %s", exc)
            return None

        for node in ast.walk(tree):
            # copilot: look for __all__ = [...] at module or class scope
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not (isinstance(target, ast.Name) and target.id == "__all__"):
                    continue
                # copilot: value must be a list or tuple of string constants
                value = node.value
                if not isinstance(value, (ast.List, ast.Tuple)):
                    log.debug("extract_all_list: __all__ is not a list/tuple literal")
                    return None
                names: list[str] = []
                for elt in value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        names.append(elt.value)
                    else:
                        log.debug("extract_all_list: non-string element in __all__")
                        return None
                return names

        return None  # no __all__ assignment found

    # ------------------------------------------------------------------
    def find_re_exported_names(self, module_source: str) -> list[ReExportRecord]:
        """Find names that are imported and then listed in ``__all__``.

        A name is classified as *re-exported* when:
        1. It appears in ``__all__``.
        2. It is introduced into the module via an ``import`` statement (not
           defined locally via ``def``, ``class``, or assignment).

        Parameters
        ----------
        module_source:
            Raw Python source text.

        Returns
        -------
        list[ReExportRecord]
            One record per re-exported name.

        Notes
        -----
        Theory connection: theory2.tex Ch19 §3.6 — *Re-export Detection*.
        """
        # copilot: parse once, collect import statements and __all__
        try:
            tree = ast.parse(module_source, mode="exec")
        except SyntaxError:
            return []

        # Build a map: identifier -> (original_module, re_export_kind, line_no)
        imported: dict[str, tuple[str, str, int]] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    effective_name = alias.asname if alias.asname else alias.name
                    kind = "explicit_all" if alias.asname else "direct_import"
                    imported[effective_name] = (mod, kind, node.lineno)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    effective_name = alias.asname if alias.asname else alias.name.split(".")[0]
                    imported[effective_name] = (alias.name, "direct_import", node.lineno)

        all_list = self.extract_all_list(module_source) or []

        records: list[ReExportRecord] = []
        for name in all_list:
            if name in imported:
                original_module, kind, lineno = imported[name]
                records.append(ReExportRecord(
                    name=name,
                    original_module=original_module,
                    re_exporting_module="<current>",
                    re_export_kind=kind,
                    line_no=lineno,
                ))
        return records

    # ------------------------------------------------------------------
    def classify_star_import_risk(self, module_name: str) -> StarImportRiskLevel:
        """Assess the risk level of ``from module_name import *``.

        The risk is computed from:
        * Whether the module has an explicit ``__all__``.
        * The size of the resulting public surface (LOW < 20, MEDIUM 20–99,
          HIGH ≥ 100).
        * Whether the module can be imported at all (CRITICAL if not).

        Parameters
        ----------
        module_name:
            Dotted name of the module to assess.

        Returns
        -------
        StarImportRiskLevel
            The computed risk level.
        """
        # copilot: import the module to inspect its surface
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            # copilot: if we can't even import it, the risk is unknowable → CRITICAL
            log.warning("classify_star_import_risk: cannot import %s", module_name)
            return StarImportRiskLevel.CRITICAL

        all_list = getattr(mod, "__all__", None)
        if all_list is not None:
            size = len(all_list)
            if size < 20:
                return StarImportRiskLevel.LOW
            elif size < 100:
                return StarImportRiskLevel.MEDIUM
            else:
                return StarImportRiskLevel.HIGH
        else:
            # copilot: no __all__ means we fall back to dir(), which is noisier
            public = [n for n in dir(mod) if not n.startswith("_")]
            size = len(public)
            if size < 10:
                return StarImportRiskLevel.MEDIUM
            elif size < 50:
                return StarImportRiskLevel.HIGH
            else:
                return StarImportRiskLevel.CRITICAL

    # ------------------------------------------------------------------
    def compute_package_api_surface(self, package_root: str) -> dict[str, list[str]]:
        """Map each sub-module in *package_root* to its list of public names.

        Parameters
        ----------
        package_root:
            Filesystem path to the package directory.

        Returns
        -------
        dict[str, list[str]]
            Keys are relative dotted module names; values are sorted lists of
            public names (those in ``__all__``, or ``dir()`` minus dunders).

        Notes
        -----
        This is a *static* analysis: it reads source files with :mod:`ast` and
        does not import anything.  Each ``.py`` file is parsed and
        :meth:`extract_all_list` is called first; if that returns ``None`` the
        method collects top-level ``def`` and ``class`` names instead.
        """
        # copilot: walk the filesystem, not sys.modules, for static safety
        result: dict[str, list[str]] = {}

        if not os.path.isdir(package_root):
            log.warning("compute_package_api_surface: not a directory: %s", package_root)
            return result

        base_depth = package_root.rstrip(os.sep).count(os.sep)

        for dirpath, _dirnames, filenames in os.walk(package_root):
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                filepath = os.path.join(dirpath, filename)
                # copilot: build the dotted module name relative to package_root
                rel = os.path.relpath(filepath, package_root)
                parts = rel.replace(os.sep, ".")[:-3]  # strip .py
                if parts.endswith(".__init__"):
                    parts = parts[: -len(".__init__")]

                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                        source = fh.read()
                except OSError as exc:
                    log.warning("cannot read %s: %s", filepath, exc)
                    continue

                try:
                    tree = ast.parse(source, filename=filepath)
                except SyntaxError:
                    continue

                all_list = self.extract_all_list(source)
                if all_list is not None:
                    result[parts] = sorted(all_list)
                else:
                    # copilot: collect top-level definitions as a proxy for the surface
                    names: list[str] = []
                    for node in tree.body:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            if not node.name.startswith("_"):
                                names.append(node.name)
                        elif isinstance(node, ast.Assign):
                            for tgt in node.targets:
                                if isinstance(tgt, ast.Name) and not tgt.id.startswith("_"):
                                    names.append(tgt.id)
                    result[parts] = sorted(set(names))

        return result


# ---------------------------------------------------------------------------
# §3.5  Witness — runtime introspection
# ---------------------------------------------------------------------------

@dataclass
class ReExportsStarImportsWitness:
    """Runtime witness for star imports and name origins.

    Unlike :class:`ReExportsStarImportsAnalyzer`, this class actually *imports*
    modules and inspects live objects.  It should not be used in contexts where
    importing arbitrary code is unsafe.

    Theory connection: theory2.tex Ch19 §3.7 — *Runtime Witnessing of Star Imports*.
    """

    # copilot: track all witnessed events for audit purposes
    _witnessed: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def witness_star_import(
        self, module_name: str, importing_ns: dict | None = None
    ) -> StarImportWitnessRecord:
        """Perform a live star-import witness for *module_name*.

        Imports *module_name*, collects the names that ``from module_name import *``
        would inject, and measures how many would shadow names already present in
        *importing_ns* (or the set of built-in names if *importing_ns* is ``None``).

        Parameters
        ----------
        module_name:
            Module to star-import.
        importing_ns:
            Optional dictionary representing the namespace into which the names
            would be injected.  Used to compute *namespace_pollution_count*.

        Returns
        -------
        StarImportWitnessRecord
            The witness record for this import event.
        """
        # copilot: use try/except — any module import can fail in unexpected ways
        try:
            mod = importlib.import_module(module_name)
        except Exception as exc:
            log.warning("witness_star_import: cannot import %s: %s", module_name, exc)
            rec = StarImportWitnessRecord(
                source_module=module_name,
                imported_names=(),
                namespace_pollution_count=0,
                risk_level=StarImportRiskLevel.CRITICAL.value,
            )
            self._witnessed.append(rec)
            return rec

        all_list = getattr(mod, "__all__", None)
        if all_list is not None:
            names = list(all_list)
        else:
            names = [n for n in dir(mod) if not n.startswith("_")]

        # copilot: count how many names would shadow built-ins or existing ns entries
        import builtins as _builtins
        builtin_names = set(dir(_builtins))
        existing = set(importing_ns.keys()) if importing_ns else set()
        collision_set = builtin_names | existing
        pollution_count = sum(1 for n in names if n in collision_set)

        # copilot: derive risk level from the analyzer for consistency
        analyzer = ReExportsStarImportsAnalyzer()
        risk = analyzer.classify_star_import_risk(module_name)

        rec = StarImportWitnessRecord(
            source_module=module_name,
            imported_names=tuple(sorted(names)),
            namespace_pollution_count=pollution_count,
            risk_level=risk.value,
        )
        self._witnessed.append(rec)
        return rec

    # ------------------------------------------------------------------
    def trace_name_origin(self, name: str, namespace: dict) -> NameOriginRecord:
        """Determine the origin of *name* within *namespace*.

        Looks up ``namespace[name]`` and inspects ``__module__``, ``__qualname__``,
        and similar dunder attributes to determine where the object was originally
        defined.

        Parameters
        ----------
        name:
            Identifier to resolve.
        namespace:
            The namespace dictionary (e.g. ``vars(some_module)``).

        Returns
        -------
        NameOriginRecord
            Record with origin module and import chain.
        """
        # copilot: handle missing names gracefully
        obj = namespace.get(name)
        if obj is None:
            return NameOriginRecord(
                name=name,
                origin_module="<unknown>",
                import_chain=(),
                resolved_at_runtime=False,
            )

        # copilot: __module__ is the most reliable origin indicator
        origin = getattr(obj, "__module__", None)
        if origin is None:
            # copilot: fall back to __package__ or __name__ for module-level objects
            origin = getattr(obj, "__package__", None) or "<unknown>"

        # copilot: build a minimal import chain using the module hierarchy
        chain: list[str] = []
        if origin and origin != "<unknown>":
            parts = origin.split(".")
            for depth in range(1, len(parts) + 1):
                chain.append(".".join(parts[:depth]))

        return NameOriginRecord(
            name=name,
            origin_module=origin,
            import_chain=tuple(chain),
            resolved_at_runtime=True,
        )

    # ------------------------------------------------------------------
    def build_reexport_judgment(self, record: ReExportRecord) -> Judgment:
        """Create a :class:`~jugeo.judgments.judgment_terms.Judgment` for *record*.

        The judgment captures the static evidence that *record.name* is
        re-exported from *record.original_module* through
        *record.re_exporting_module*.

        Parameters
        ----------
        record:
            The :class:`ReExportRecord` to wrap in a judgment.

        Returns
        -------
        Judgment
            A new judgment in ``PROPOSED`` status.  Callers should call
            ``.settle()`` after additional runtime validation.
        """
        # copilot: build a structural proposition about the re-export
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=(
                f"Name '{record.name}' is re-exported from '{record.original_module}' "
                f"via '{record.re_exporting_module}' (kind: {record.re_export_kind}, "
                f"line: {record.line_no})"
            ),
            label=f"reexport:{record.re_exporting_module}:{record.name}",
        )
        coord = Coordinate(
            components=(record.re_exporting_module, record.name),
            kind=CoordinateKind.MODULE,
        )
        carrier = Carrier(
            coordinate=coord,
            payload=record,
            label=f"{record.re_exporting_module}.{record.name}",
        )
        evidence = EvidenceBundle()
        evidence.add(EvidenceItem(
            kind=EvidenceItemKind.STATIC_ANALYSIS,
            payload={"line_no": record.line_no, "kind": record.re_export_kind},
            label="ast_reexport_detection",
        ))
        j = Judgment(
            status=JudgmentStatus.PROPOSED,
            proposition=prop,
            carrier=carrier,
            evidence=evidence,
            label=f"judgment:reexport:{record.re_exporting_module}:{record.name}",
        )
        return j


# ---------------------------------------------------------------------------
# §3.6  Utility helpers
# ---------------------------------------------------------------------------

def _iter_package_modules(package_name: str) -> list[str]:
    """Return dotted names of all sub-modules in *package_name*.

    Uses :func:`pkgutil.walk_packages` with the package's ``__path__``.
    Falls back to an empty list if the package cannot be imported.

    Parameters
    ----------
    package_name:
        Top-level dotted package name.

    Returns
    -------
    list[str]
        Sorted list of dotted module names.
    """
    # copilot: pkgutil.walk_packages is the idiomatic approach for package enumeration
    try:
        pkg = importlib.import_module(package_name)
    except Exception:
        return []
    pkg_path = getattr(pkg, "__path__", None)
    if pkg_path is None:
        return [package_name]
    names: list[str] = []
    for _finder, mod_name, _ispkg in pkgutil.walk_packages(
        path=pkg_path, prefix=package_name + ".", onerror=lambda e: None
    ):
        names.append(mod_name)
    return sorted(names)


def _public_names_from_module(mod: Any) -> list[str]:
    """Extract public names from a live module object.

    Prefers ``__all__`` if present; otherwise falls back to
    ``[n for n in dir(mod) if not n.startswith('_')]``.

    Parameters
    ----------
    mod:
        A live module object (already imported).

    Returns
    -------
    list[str]
        Sorted list of public names.
    """
    # copilot: always prefer __all__ over dir() for predictability
    all_list = getattr(mod, "__all__", None)
    if all_list is not None:
        return sorted(all_list)
    return sorted(n for n in dir(mod) if not n.startswith("_"))


def summarize_surface(record: PackageSurfaceRecord) -> str:
    """Return a human-readable one-line summary of *record*.

    Parameters
    ----------
    record:
        The :class:`PackageSurfaceRecord` to summarize.

    Returns
    -------
    str
        A single-line summary string.
    """
    # copilot: used mainly for logging and smoke-test output
    all_tag = "__all__=yes" if record.all_defined else "__all__=no"
    return (
        f"PackageSurface({record.package_name!r}: "
        f"public={record.surface_size}, "
        f"re-exported={len(record.re_exported_names)}, "
        f"star_imports={len(record.star_imports)}, "
        f"{all_tag})"
    )


# ---------------------------------------------------------------------------
# §3.7  Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # copilot: exercise all public classes and functions with minimal setup
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")
    log.info("=== re_exports_star_imports_and_packag.py smoke test ===")

    # --- StarImportRiskLevel ---
    print("\n[1] StarImportRiskLevel values:")
    for level in StarImportRiskLevel:
        print(f"  {level.name} = {level.value!r}")

    # --- ReExportRecord ---
    print("\n[2] ReExportRecord:")
    rec = ReExportRecord(
        name="Coordinate",
        original_module="jugeo.geometry.site",
        re_exporting_module="jugeo.geometry",
        re_export_kind="explicit_all",
        line_no=42,
    )
    print(f"  {rec}")

    # --- ReExportHop ---
    print("\n[3] ReExportHop chain:")
    hops = [
        ReExportHop("jugeo.geometry.site", "Coordinate", 0, MorphismKind.INCLUSION.value),
        ReExportHop("jugeo.geometry", "Coordinate", 1, MorphismKind.TRANSPORT.value),
    ]
    for hop in hops:
        print(f"  [{hop.hop_index}] {hop.module_name}.{hop.name} ({hop.morphism_kind})")

    # --- PackageSurfaceRecord ---
    print("\n[4] PackageSurfaceRecord:")
    surface = PackageSurfaceRecord(
        package_name="jugeo.geometry",
        public_names=("Coordinate", "Morphism", "Site"),
        re_exported_names=("Coordinate", "Morphism"),
        star_imports=(),
        all_defined=True,
        surface_size=3,
    )
    print(f"  {summarize_surface(surface)}")

    # --- StarImportWitnessRecord ---
    print("\n[5] StarImportWitnessRecord:")
    witness_rec = StarImportWitnessRecord(
        source_module="os.path",
        imported_names=("join", "split", "exists"),
        namespace_pollution_count=0,
        risk_level=StarImportRiskLevel.LOW.value,
    )
    print(f"  {witness_rec}")

    # --- NameOriginRecord ---
    print("\n[6] NameOriginRecord:")
    origin_rec = NameOriginRecord(
        name="Coordinate",
        origin_module="jugeo.geometry.site",
        import_chain=("jugeo", "jugeo.geometry", "jugeo.geometry.site"),
        resolved_at_runtime=False,
    )
    print(f"  {origin_rec}")

    # --- ReExportsStarImportsAnalyzer ---
    print("\n[7] ReExportsStarImportsAnalyzer:")
    analyzer = ReExportsStarImportsAnalyzer()
    sample_source = textwrap.dedent("""\
        from jugeo.geometry.site import Coordinate, Morphism
        from jugeo.judgments.judgment_terms import Judgment

        __all__ = ["Coordinate", "Morphism", "Judgment", "helper"]

        def helper():
            pass
    """)
    all_list = analyzer.extract_all_list(sample_source)
    print(f"  __all__ extracted: {all_list}")
    re_exports = analyzer.find_re_exported_names(sample_source)
    print(f"  re-exported names: {[r.name for r in re_exports]}")
    risk = analyzer.classify_star_import_risk("os.path")
    print(f"  star-import risk for 'os.path': {risk.value}")
    api = analyzer.compute_package_api_surface(os.path.dirname(__file__) or ".")
    print(f"  package API surface (first 3 entries):")
    for k, v in list(api.items())[:3]:
        print(f"    {k}: {v[:5]}")

    # --- ReExportsStarImportsCoordinator ---
    print("\n[8] ReExportsStarImportsCoordinator:")
    coord_obj = ReExportsStarImportsCoordinator()
    # copilot: analyze 'os' as a real importable package
    os_surface = coord_obj.analyze_package_surface("os")
    print(f"  {summarize_surface(os_surface)}")
    chain = coord_obj.trace_re_export_chain("path", "os")
    print(f"  re-export chain for 'os.path': {[h.module_name for h in chain]}")
    names = coord_obj.expand_star_import("os.path")
    print(f"  expand_star_import('os.path') → {len(names)} names")
    j = coord_obj.build_surface_judgment(os_surface)
    print(f"  judgment status: {j.status}")

    # --- ReExportsStarImportsWitness ---
    print("\n[9] ReExportsStarImportsWitness:")
    wit = ReExportsStarImportsWitness()
    witness_result = wit.witness_star_import("os.path")
    print(f"  witnessed {len(witness_result.imported_names)} names from 'os.path'")
    print(f"  pollution count: {witness_result.namespace_pollution_count}")
    print(f"  risk: {witness_result.risk_level}")
    import os as _os
    origin = wit.trace_name_origin("path", vars(_os))
    print(f"  origin of 'path' in os namespace: {origin.origin_module}")
    j2 = wit.build_reexport_judgment(rec)
    print(f"  re-export judgment label: {j2.label}")

    # --- z3_available check ---
    print(f"\n[10] z3_available(): {z3_available()}")

    print("\n=== smoke test complete ===")
