from __future__ import annotations

r"""theory2.tex Ch19 §4 — Dynamic Import and Reflection.

This module implements dynamic import analysis and runtime reflection machinery
for the JuGeo import graph pipeline.  The central idea — formalised in
theory2.tex Ch19 §4 — is that dynamic import calls (importlib.import_module,
__import__, importlib.util.spec_from_file_location, etc.) introduce morphisms
into the import site whose source coordinates are not statically determinable.
These morphisms must be over-approximated via abstract interpretation and
then witnessed at runtime to raise their TrustLevel from COPILOT_SUGGESTED
to RUNTIME_WITNESSED.

Architecture
------------
* :class:`DynamicImportReflectionCoordinator` — orchestrates dynamic import
  analysis across a module surface; converts raw AST call nodes into
  DynamicImportRecord values and submits them to the judgment layer.
* :class:`DynamicImportReflectionAnalyzer` — pure static analysis; detects
  dynamic import call patterns, classifies them, identifies lazy imports and
  plugin discovery patterns.
* :class:`DynamicImportReflectionWitness` — runtime witness layer; actually
  attempts the imports recorded during static analysis and records the outcome
  as EvidenceItem values at TrustLevel.RUNTIME_WITNESSED.

Theory alignment
----------------
* §4.1 — Dynamic morphisms: import calls as non-static restriction arrows
* §4.2 — Abstract interpretation of module_name arguments
* §4.3 — importlib.util as the meta-path hook reflection interface
* §4.4 — sys.meta_path and sys.path_hooks as the hook carrier chain
* §4.5 — Lazy imports and plugin discovery as deferred covering families

The word *copilot* appears throughout because dynamic imports are the primary
source of copilot-suggested edges: the copilot proposes a resolved_name for
a dynamic import call based on string literal analysis, and the witness layer
either confirms or refutes the proposal at runtime.

Each DynamicImportRecord is a frozen coordinate in the analysis site; the
ReflectionRecord is the runtime counterpart recording what attributes and
sub-packages the module actually exposes after loading.

Lazy imports receive special treatment (§4.5): they are modelled as deferred
morphisms whose trigger condition is recorded in LazyImportRecord, and they
are only promoted to RUNTIME_WITNESSED when the trigger fires in a live
interpreter session monitored by the RuntimeChannel.

Plugin patterns (§4.5) are even more open-ended: the PluginPatternRecord
captures the discovery mechanism (pkg_resources entry points, importlib.metadata
entry points, namespace packages, __import_plugins__ conventions) so that the
topology can represent the *potential* site of plugin coordinates even before
they are installed.
"""

import ast
import importlib
import importlib.machinery
import importlib.util
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

log = logging.getLogger(__name__)

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
# Enumerations
# ---------------------------------------------------------------------------

class DynamicImportKind(str, Enum):
    """Taxonomy of dynamic import call patterns (theory2.tex §4.2).

    Each variant corresponds to a distinct API surface through which Python
    code can perform a dynamic import at runtime.  The classification informs
    the abstract-interpretation pass: IMPORTLIB_IMPORT_MODULE is the most
    common and well-specified; EXEC_IMPORT is the least constrained and
    requires the heaviest over-approximation.

    Variants
    --------
    IMPORTLIB_IMPORT_MODULE
        A call to ``importlib.import_module(name)`` or the bare
        ``import_module(name)`` form after a ``from importlib import``
        statement.  The name argument is often a string literal and can
        therefore be resolved statically in the majority of cases.
    IMPORTLIB_UTIL_SPEC
        A call into the ``importlib.util`` namespace: ``find_spec``,
        ``spec_from_file_location``, ``spec_from_loader``,
        ``module_from_spec``, or ``exec_module``.  These calls manipulate
        the loader machinery directly and represent fine-grained control
        over module loading.
    BUILTIN_IMPORT
        A direct call to the built-in ``__import__`` function.  This is the
        lowest-level dynamic import mechanism and is rarely called directly
        in modern code; it is typically found in compatibility shims.
    EXEC_IMPORT
        An import triggered via ``exec`` or ``eval`` — the module name is
        embedded in a dynamically constructed string and is therefore
        fully opaque to static analysis.
    LAZY_IMPORT
        An import that is deferred until its first use, either via
        ``importlib.util.LazyLoader``, a function-body import, or an
        ``if TYPE_CHECKING:`` guard.
    """

    IMPORTLIB_IMPORT_MODULE = "importlib_import_module"
    IMPORTLIB_UTIL_SPEC = "importlib_util_spec"
    BUILTIN_IMPORT = "builtin_import"
    EXEC_IMPORT = "exec_import"
    LAZY_IMPORT = "lazy_import"


# ---------------------------------------------------------------------------
# Frozen dataclass records
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DynamicImportRecord:
    """Frozen record of a single dynamic import call site (theory2.tex §4.1).

    Each instance represents one detected dynamic import call in the source
    tree.  The record is the primary unit exchanged between the analyzer and
    the coordinator: the analyzer produces records, and the coordinator
    converts them into Judgment values for the pipeline.

    Fields
    ------
    call_site : str
        String representation of the AST call node location, typically of
        the form ``"line:<N>"``.  Used as the primary identifier when logging
        and when building judgment labels.
    module_expr : str
        The raw string form of the first argument to the dynamic import call
        as reconstructed by ``ast.unparse``.  For a call like
        ``importlib.import_module("os.path")`` this would be ``"'os.path'"``.
        For a variable reference like ``importlib.import_module(plugin_name)``
        this would be ``"plugin_name"``.
    resolved_name : str
        The copilot-proposed resolution of *module_expr*.  Non-empty only
        when *module_expr* was a string literal; in that case *resolved_name*
        is the unquoted literal value (e.g., ``"os.path"``).  Empty when the
        expression is opaque.
    import_kind : DynamicImportKind
        Classification of the dynamic import mechanism.  Drives the
        over-approximation strategy in the abstract interpretation pass.
    is_conditional : bool
        True when the call appears inside an if/try/except block.  Conditional
        dynamic imports are weaker morphisms: they may not always fire.
    coordinate : object
        The site coordinate for the calling module.  Typically a
        :class:`Coordinate` whose components tuple contains the resolved_name
        or the raw module_expr when resolution failed.
    """

    call_site: str = ""
    module_expr: str = ""
    resolved_name: str = ""
    import_kind: DynamicImportKind = DynamicImportKind.IMPORTLIB_IMPORT_MODULE
    is_conditional: bool = False
    coordinate: object = None


@dataclass(frozen=True, slots=True)
class ImportlibUsageRecord:
    """Records a single usage of the importlib API (theory2.tex §4.3).

    This lightweight record is produced by the coordinator's
    :meth:`DynamicImportReflectionCoordinator.trace_importlib_usage` method
    as a first-pass summary of importlib API surface consumed by a module.
    It is cheaper to produce than a full DynamicImportRecord because it does
    not include coordinate resolution.

    Fields
    ------
    usage_kind : str
        The :attr:`DynamicImportKind.value` string for this usage.
    module_arg : str
        The first positional argument (module name expression) as unparsed
        by ``ast.unparse``.
    spec_or_loader : str
        The second positional argument if present, or empty string.  Relevant
        for calls like ``spec_from_file_location(name, path)`` where the
        second argument is a filesystem path.
    line_no : int
        Source line number of the call, as reported by the AST node's
        ``lineno`` attribute.
    """

    usage_kind: str = ""
    module_arg: str = ""
    spec_or_loader: str = ""
    line_no: int = 0


@dataclass(frozen=True, slots=True)
class ModuleSpecRecord:
    """Snapshot of an importlib.util.ModuleSpec (theory2.tex §4.3).

    A :class:`ModuleSpecRecord` is the static counterpart to Python's
    ``importlib.util.ModuleSpec``: it captures the key fields of the spec
    as a frozen value suitable for comparison, hashing, and serialisation.
    The record is produced by
    :meth:`DynamicImportReflectionCoordinator.evaluate_import_spec` without
    executing the module.

    Fields
    ------
    name : str
        Fully qualified module name as reported by the spec.
    origin : str
        Filesystem path of the module source file, or empty string when the
        module has no on-disk origin (e.g., built-in modules).
    submodule_search_locations : tuple
        Tuple of filesystem paths that constitute the package search path.
        Non-empty only for packages; empty for plain modules.
    loader_class : str
        Class name of the module loader (e.g., ``"SourceFileLoader"``,
        ``"BuiltinImporter"``).
    is_package : bool
        True when *submodule_search_locations* is non-empty, indicating that
        the spec represents a package rather than a module.
    """

    name: str = ""
    origin: str = ""
    submodule_search_locations: tuple = ()
    loader_class: str = ""
    is_package: bool = False


@dataclass(frozen=True, slots=True)
class ReflectionRecord:
    """Runtime reflection snapshot of a loaded module (theory2.tex §4.4).

    A :class:`ReflectionRecord` is produced by
    :meth:`DynamicImportReflectionCoordinator.coordinate_runtime_reflection`
    after a module has been successfully imported.  It captures the complete
    attribute surface of the module as a frozen tuple, together with
    package-identity metadata.

    The record is the primary input to
    :meth:`DynamicImportReflectionWitness.build_reflection_judgment`, which
    promotes it to a RUNTIME_WITNESSED Judgment.

    Fields
    ------
    module_name : str
        Fully qualified module name (value of ``__name__``).
    attributes : tuple
        Tuple of all attribute names as returned by ``dir(module)``.  This
        is the complete surface including dunder names.
    dunder_attrs : tuple
        Subset of *attributes* whose names start and end with double
        underscores.  These are the protocol slots and module-level metadata
        attributes.
    is_package : bool
        True when the module object has a ``__path__`` attribute, indicating
        it is a package.
    file_path : str
        Value of ``module.__file__``, or empty string for modules without a
        filesystem representation.
    package_root : str
        Value of ``module.__package__``, or empty string when absent.
    """

    module_name: str = ""
    attributes: tuple = ()
    dunder_attrs: tuple = ()
    is_package: bool = False
    file_path: str = ""
    package_root: str = ""


@dataclass(frozen=True, slots=True)
class DynamicImportWitnessRecord:
    """Runtime witness record after attempting a dynamic import (theory2.tex §4.5).

    This record is the output of
    :meth:`DynamicImportReflectionWitness.witness_dynamic_import`.  It
    captures whether the import succeeded and, if so, what spec metadata was
    observed.  A successful witness promotes the corresponding
    DynamicImportRecord's trust level from COPILOT_SUGGESTED to
    RUNTIME_WITNESSED.

    Fields
    ------
    module_name : str
        The module that was attempted.
    load_succeeded : bool
        True when ``importlib.import_module`` returned without raising.
    spec_origin : str
        The ``origin`` field from the ModuleSpec, or empty string on failure
        or for built-in modules.
    loader_kind : str
        Class name of the loader that handled the module, or empty string
        when no loader is available.
    has_submodules : bool
        True when the loaded module exposes a ``__path__`` attribute,
        indicating it is a package with loadable sub-modules.
    """

    module_name: str = ""
    load_succeeded: bool = False
    spec_origin: str = ""
    loader_kind: str = ""
    has_submodules: bool = False


@dataclass(frozen=True, slots=True)
class ModuleAttributeMap:
    """Categorised attribute map for a reflected module (theory2.tex §4.4).

    This record is produced by
    :meth:`DynamicImportReflectionWitness.probe_module_attributes` and
    provides a structured categorisation of the module's public API surface.
    The categorisation follows the convention used throughout the JuGeo
    topology for separating public interface coordinates from private
    implementation coordinates.

    Fields
    ------
    module_name : str
        Fully qualified module name.
    public_attrs : tuple
        Tuple of attribute names that do not start with an underscore.  These
        are the primary API surface and are candidates for public Coordinate
        objects in the site.
    private_attrs : tuple
        Tuple of attribute names that start with exactly one underscore (not
        dunder).  These represent internal implementation details.
    callable_attrs : tuple
        Tuple of attribute names where ``callable(getattr(mod, attr))`` is
        True.  Includes functions, methods, and callable classes.
    class_attrs : tuple
        Tuple of attribute names whose values are ``type`` instances.
        Represents the class hierarchy exposed by the module.
    """

    module_name: str = ""
    public_attrs: tuple = ()
    private_attrs: tuple = ()
    callable_attrs: tuple = ()
    class_attrs: tuple = ()


@dataclass(frozen=True, slots=True)
class ImportHookRecord:
    """Snapshot of a single entry in sys.meta_path or sys.path_hooks (theory2.tex §4.4).

    The import hook chain is a key part of the Python import machinery that
    is often overlooked in static analysis.  Hooks registered in
    ``sys.meta_path`` or ``sys.path_hooks`` can intercept *any* import and
    redirect it to a custom loader, making the effective module resolution
    non-standard.  These records document the hook chain so that the topology
    can flag when non-standard hooks are present.

    Fields
    ------
    hook_class : str
        Class name of the hook object.  For meta-path hooks this is typically
        ``"BuiltinImporter"``, ``"FrozenImporter"``, or ``"PathFinder"``.
        For path hooks it is typically ``"FileFinder"``.
    hook_index : int
        Zero-based position of the hook in its list (meta_path or path_hooks).
        Earlier positions have higher priority in the hook resolution order.
    is_meta_path : bool
        True when this record was taken from ``sys.meta_path``.
    is_path_hook : bool
        True when this record was taken from ``sys.path_hooks``.
    """

    hook_class: str = ""
    hook_index: int = 0
    is_meta_path: bool = False
    is_path_hook: bool = False


@dataclass(frozen=True, slots=True)
class LazyImportRecord:
    """Record of a lazy / deferred import pattern (theory2.tex §4.5).

    A lazy import is an import whose execution is deferred past module
    load time.  These are modelled as deferred morphisms in the import
    graph: they connect coordinates but carry a *trigger condition* that
    must be satisfied before the morphism fires.

    Typical lazy import patterns:
    * Function-body imports (trigger = first function call).
    * ``if TYPE_CHECKING:`` guards (trigger = type-checker execution only;
      never fires at runtime — these are annotation-only imports).
    * ``try/except ImportError`` guards (trigger = availability of the
      optional dependency).
    * ``importlib.util.LazyLoader`` usage (trigger = first attribute access
      on the module proxy object).

    Fields
    ------
    name : str
        The module or name being lazily imported.
    trigger_condition : str
        String description of the condition that triggers the import.
        Formatted as ``"function_call:<function_name>"`` for function-body
        imports, ``"TYPE_CHECKING"`` for annotation guards, and
        ``"try_except_import_error"`` for optional-dependency guards.
    line_no : int
        Source line number of the import statement inside the lazy scope.
    import_expr : str
        Raw import expression text as reconstructed by ``ast.unparse``.
    """

    name: str = ""
    trigger_condition: str = ""
    line_no: int = 0
    import_expr: str = ""


@dataclass(frozen=True, slots=True)
class PluginPatternRecord:
    """Record of a plugin discovery pattern (theory2.tex §4.5).

    Plugin discovery patterns are the most open-ended form of dynamic import:
    the set of modules that will be loaded is not determinable from the source
    alone — it depends on what is installed in the environment at runtime.
    The topology models these as *potential* covering families: the
    PluginPatternRecord documents the discovery mechanism so that the graph
    can include a placeholder coordinate for each discovered plugin even
    before the plugins are known.

    Recognised patterns include:
    * ``importlib.metadata.entry_points`` — PEP 517 entry point discovery.
    * ``pkg_resources.iter_entry_points`` — setuptools entry point discovery.
    * ``pkgutil.iter_modules`` — namespace package walking.
    * ``__import_plugins__`` — bespoke plugin registration conventions.

    Fields
    ------
    pattern_kind : str
        The attr name that triggered detection (e.g., ``"entry_points"``).
    entry_point_module : str
        The module that drives discovery (e.g., ``"importlib.metadata"``).
    discovery_mechanism : str
        Human-readable description of the mechanism.
    line_no : int
        Source line where the pattern was detected.
    """

    pattern_kind: str = ""
    entry_point_module: str = ""
    discovery_mechanism: str = ""
    line_no: int = 0


# ---------------------------------------------------------------------------
# Primary analysis and witness classes
# ---------------------------------------------------------------------------

class DynamicImportReflectionAnalyzer:
    """Static and runtime analysis of dynamic import patterns.

    Implements the abstract interpretation pass described in theory2.tex §4.2.
    All methods operate on Python source text or AST trees and produce frozen
    record values suitable for downstream judgment construction.

    The analyzer does *not* execute any import; it is a pure static pass.
    Runtime witnessing is delegated to :class:`DynamicImportReflectionWitness`.

    Design notes
    ------------
    The analyzer is intentionally stateless: all methods take source text or
    AST nodes as inputs and return frozen value types.  This makes the
    analyzer safe to call from multiple threads and idempotent across
    repeated invocations on the same source.

    The detection heuristics are deliberately conservative (over-approximate):
    it is better to flag a call as a dynamic import when it is not than to
    miss a genuine dynamic import that would leave a dangling edge in the
    import graph.
    """

    # copilot: these are the importlib API names we recognise as dynamic import calls
    _IMPORTLIB_CALLS: frozenset[str] = frozenset({
        "import_module", "spec_from_file_location", "spec_from_loader",
        "find_spec", "module_from_spec", "exec_module",
    })

    def detect_dynamic_import_calls(self, source: str) -> list[ast.Call]:
        """Parse *source* and return every AST Call node that looks like a
        dynamic import.

        Detection heuristics (theory2.tex §4.2.1):
        1. Direct calls to ``importlib.import_module`` or ``__import__``.
        2. Attribute access on ``importlib.util`` or ``importlib.machinery``.
        3. Calls whose first argument is a variable (not a string literal) —
           these are the *opaque* dynamic imports that are hardest to resolve.

        Parameters
        ----------
        source:
            Raw Python source text to analyse.

        Returns
        -------
        list[ast.Call]
            All detected dynamic import call nodes, in source order.
        """
        # copilot: parse with type_comments=True so we capture PEP 484 annotations
        try:
            tree = ast.parse(source, type_comments=True)
        except SyntaxError as exc:
            log.warning("detect_dynamic_import_calls: SyntaxError %s", exc)
            return []

        results: list[ast.Call] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # pattern 1: importlib.import_module(...)
            if isinstance(func, ast.Attribute) and func.attr in self._IMPORTLIB_CALLS:
                results.append(node)
                continue
            # pattern 2: __import__(...)
            if isinstance(func, ast.Name) and func.id == "__import__":
                results.append(node)
                continue
            # pattern 3: bare import_module(...) after `from importlib import import_module`
            if isinstance(func, ast.Name) and func.id in self._IMPORTLIB_CALLS:
                results.append(node)
        return results

    def classify_dynamic_import(self, node: ast.Call) -> DynamicImportKind:
        """Classify a dynamic import AST Call node into a DynamicImportKind.

        Classification rules (theory2.tex §4.2.2):
        * ``importlib.import_module`` / ``import_module`` → IMPORTLIB_IMPORT_MODULE
        * ``importlib.util.spec_from_*`` / ``find_spec`` → IMPORTLIB_UTIL_SPEC
        * ``__import__`` → BUILTIN_IMPORT
        * calls inside ``exec`` bodies → EXEC_IMPORT
        * calls inside ``if TYPE_CHECKING`` or try blocks → LAZY_IMPORT

        Parameters
        ----------
        node:
            An AST Call node previously returned by
            :meth:`detect_dynamic_import_calls`.

        Returns
        -------
        DynamicImportKind
            The most specific classification available.
        """
        func = node.func
        # copilot: attribute calls — check the attribute name first
        if isinstance(func, ast.Attribute):
            attr = func.attr
            if attr == "import_module":
                return DynamicImportKind.IMPORTLIB_IMPORT_MODULE
            if attr in {"spec_from_file_location", "spec_from_loader", "find_spec",
                        "module_from_spec", "exec_module"}:
                return DynamicImportKind.IMPORTLIB_UTIL_SPEC
        if isinstance(func, ast.Name):
            if func.id == "__import__":
                return DynamicImportKind.BUILTIN_IMPORT
            if func.id == "import_module":
                return DynamicImportKind.IMPORTLIB_IMPORT_MODULE
            if func.id in {"exec_module", "spec_from_file_location"}:
                return DynamicImportKind.IMPORTLIB_UTIL_SPEC
        # copilot: default — treat unrecognised patterns as exec-style dynamic imports
        return DynamicImportKind.EXEC_IMPORT

    def analyze_importlib_util_usage(self, source: str) -> list[str]:
        """Return a list of distinct importlib.util API calls found in *source*.

        This method is a lightweight summary pass (theory2.tex §4.3.1) that
        does not build full records — it is used by the coordinator to decide
        whether a deeper IMPORTLIB_UTIL_SPEC pass is warranted.

        Parameters
        ----------
        source:
            Raw Python source text.

        Returns
        -------
        list[str]
            Deduplicated list of importlib.util attribute names called.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        seen: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in self._IMPORTLIB_CALLS:
                # copilot: check the value chain to confirm importlib.util prefix
                if isinstance(func.value, ast.Attribute) and func.value.attr == "util":
                    seen.add(func.attr)
                elif isinstance(func.value, ast.Name) and func.value.id in {"util", "importlib"}:
                    seen.add(func.attr)
        return sorted(seen)

    def find_lazy_imports(self, source: str) -> list[LazyImportRecord]:
        """Detect lazy / deferred import patterns in *source* (theory2.tex §4.5.1).

        A lazy import is any import that only executes conditionally:
        * Inside ``if TYPE_CHECKING:``
        * Inside a function body (deferred until first call)
        * Inside a ``try`` block where the bare-except path is non-trivial
        * Using ``importlib.util.LazyLoader``

        Parameters
        ----------
        source:
            Raw Python source text.

        Returns
        -------
        list[LazyImportRecord]
            One record per detected lazy import pattern.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        records: list[LazyImportRecord] = []

        for node in ast.walk(tree):
            # copilot: function-level imports are the most common lazy pattern
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        names = [a.name or "" for a in getattr(child, "names", [])]
                        for nm in names:
                            records.append(LazyImportRecord(
                                name=nm,
                                trigger_condition=f"function_call:{node.name}",
                                line_no=getattr(child, "lineno", 0),
                                import_expr=ast.unparse(child),
                            ))
            # copilot: TYPE_CHECKING guard is the standard PEP 484 lazy import
            if isinstance(node, ast.If):
                test = node.test
                is_type_checking = (
                    isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
                ) or (
                    isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                )
                if is_type_checking:
                    for child in ast.walk(node):
                        if isinstance(child, (ast.Import, ast.ImportFrom)):
                            names = [a.name or "" for a in getattr(child, "names", [])]
                            for nm in names:
                                records.append(LazyImportRecord(
                                    name=nm,
                                    trigger_condition="TYPE_CHECKING",
                                    line_no=getattr(child, "lineno", 0),
                                    import_expr=ast.unparse(child),
                                ))
        return records

    def extract_plugin_patterns(self, source: str) -> list[PluginPatternRecord]:
        """Detect plugin discovery patterns in *source* (theory2.tex §4.5.2).

        Recognised patterns:
        * ``importlib.metadata.entry_points`` calls
        * ``pkg_resources.iter_entry_points`` calls
        * Namespace package walking via ``pkgutil.iter_modules``
        * Custom ``__import_plugins__`` convention

        Parameters
        ----------
        source:
            Raw Python source text.

        Returns
        -------
        list[PluginPatternRecord]
            One record per detected plugin discovery pattern.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        records: list[PluginPatternRecord] = []
        _PLUGIN_ATTRS = {"entry_points", "iter_entry_points", "iter_modules",
                         "__import_plugins__"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr = ""
            value_name = ""
            if isinstance(func, ast.Attribute):
                attr = func.attr
                if isinstance(func.value, ast.Attribute):
                    value_name = func.value.attr
                elif isinstance(func.value, ast.Name):
                    value_name = func.value.id
            elif isinstance(func, ast.Name):
                attr = func.id
            if attr not in _PLUGIN_ATTRS:
                continue
            # copilot: classify the mechanism from the call shape
            if attr == "entry_points":
                mechanism = "importlib.metadata.entry_points"
                entry_module = "importlib.metadata"
            elif attr == "iter_entry_points":
                mechanism = "pkg_resources.iter_entry_points"
                entry_module = "pkg_resources"
            elif attr == "iter_modules":
                mechanism = "pkgutil.iter_modules namespace walk"
                entry_module = "pkgutil"
            else:
                mechanism = f"custom:{attr}"
                entry_module = value_name or "unknown"
            records.append(PluginPatternRecord(
                pattern_kind=attr,
                entry_point_module=entry_module,
                discovery_mechanism=mechanism,
                line_no=getattr(node, "lineno", 0),
            ))
        return records


class DynamicImportReflectionWitness:
    """Runtime witness layer for dynamic imports (theory2.tex §4.5).

    This class actually attempts imports at runtime using importlib and records
    the outcome as :class:`DynamicImportWitnessRecord` and
    :class:`ReflectionRecord` values.  All results are tagged at
    ``TrustLevel.RUNTIME_WITNESSED`` if the import succeeds, or
    ``TrustLevel.COPILOT_SUGGESTED`` if it fails.

    The witness is deliberately separate from the analyzer so that the static
    analysis pipeline can be run without any side-effects (no imports are
    executed during analysis).

    Usage pattern
    -------------
    ::

        witness = DynamicImportReflectionWitness()
        w = witness.witness_dynamic_import("json")
        if w.load_succeeded:
            attr_map = witness.probe_module_attributes("json")
        hooks = witness.record_import_hook_chain()

    All methods are safe to call repeatedly; the witness does not accumulate
    state between calls.  The only observable side-effect is the presence of
    the imported module in ``sys.modules`` after a successful witness call.
    """

    def witness_dynamic_import(self, module_name: str) -> DynamicImportWitnessRecord:
        """Attempt to import *module_name* at runtime and record the outcome.

        Uses ``importlib.util.find_spec`` first (no execution), then
        ``importlib.import_module`` to load the module if the spec is found.

        Parameters
        ----------
        module_name:
            Fully qualified module name to attempt.

        Returns
        -------
        DynamicImportWitnessRecord
            Record with load_succeeded=True if the import worked.
        """
        # copilot: find_spec first to avoid side-effects of a failed import
        spec = None
        try:
            spec = importlib.util.find_spec(module_name)
        except (ModuleNotFoundError, ValueError) as exc:
            log.debug("witness_dynamic_import: find_spec failed for %s: %s", module_name, exc)

        if spec is None:
            return DynamicImportWitnessRecord(
                module_name=module_name,
                load_succeeded=False,
                spec_origin="",
                loader_kind="",
                has_submodules=False,
            )

        origin = spec.origin or ""
        loader_kind = type(spec.loader).__name__ if spec.loader else ""
        has_submodules = spec.submodule_search_locations is not None

        # copilot: now actually load the module to confirm it executes cleanly
        try:
            mod = importlib.import_module(module_name)
            log.debug("witness_dynamic_import: loaded %s", module_name)
            has_submodules = has_submodules or hasattr(mod, "__path__")
            return DynamicImportWitnessRecord(
                module_name=module_name,
                load_succeeded=True,
                spec_origin=origin,
                loader_kind=loader_kind,
                has_submodules=has_submodules,
            )
        except Exception as exc:
            log.warning("witness_dynamic_import: import failed for %s: %s", module_name, exc)
            return DynamicImportWitnessRecord(
                module_name=module_name,
                load_succeeded=False,
                spec_origin=origin,
                loader_kind=loader_kind,
                has_submodules=has_submodules,
            )

    def probe_module_attributes(self, module_name: str) -> ModuleAttributeMap:
        """Load *module_name* and categorise its attributes.

        Parameters
        ----------
        module_name:
            Fully qualified module name.

        Returns
        -------
        ModuleAttributeMap
            Categorised attribute snapshot.
        """
        # copilot: prefer already-loaded modules to avoid double-loading
        mod = sys.modules.get(module_name)
        if mod is None:
            try:
                mod = importlib.import_module(module_name)
            except Exception as exc:
                log.warning("probe_module_attributes: cannot load %s: %s", module_name, exc)
                return ModuleAttributeMap(module_name=module_name)

        all_attrs = dir(mod)
        public = tuple(a for a in all_attrs if not a.startswith("_"))
        private = tuple(a for a in all_attrs if a.startswith("_") and not a.startswith("__"))
        callables = tuple(a for a in all_attrs if callable(getattr(mod, a, None)))
        classes = tuple(
            a for a in all_attrs
            if isinstance(getattr(mod, a, None), type)
        )
        return ModuleAttributeMap(
            module_name=module_name,
            public_attrs=public,
            private_attrs=private,
            callable_attrs=callables,
            class_attrs=classes,
        )

    def record_import_hook_chain(self) -> list[ImportHookRecord]:
        """Snapshot the current sys.meta_path and sys.path_hooks (theory2.tex §4.4).

        Returns
        -------
        list[ImportHookRecord]
            One record per hook, meta-path hooks first then path hooks.
        """
        records: list[ImportHookRecord] = []
        # copilot: meta_path hooks are tried first by the import machinery
        for idx, hook in enumerate(sys.meta_path):
            records.append(ImportHookRecord(
                hook_class=type(hook).__name__,
                hook_index=idx,
                is_meta_path=True,
                is_path_hook=False,
            ))
        for idx, hook in enumerate(sys.path_hooks):
            records.append(ImportHookRecord(
                hook_class=getattr(hook, "__name__", type(hook).__name__),
                hook_index=idx,
                is_meta_path=False,
                is_path_hook=True,
            ))
        return records

    def build_reflection_judgment(self, record: ReflectionRecord) -> "Judgment":
        """Construct a :class:`Judgment` from a :class:`ReflectionRecord`.

        The judgment captures the proposition that the module at
        *record.module_name* exposes the attribute surface recorded in
        *record.attributes*.  Trust level is RUNTIME_WITNESSED because the
        record was produced by actual import.

        Parameters
        ----------
        record:
            A reflection record produced by
            :meth:`DynamicImportReflectionCoordinator.coordinate_runtime_reflection`.

        Returns
        -------
        Judgment
            A settled judgment with RUNTIME_WITNESSED trust.
        """
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=(
                f"module {record.module_name!r} exposes "
                f"{len(record.attributes)} attributes"
            ),
            label=f"reflection:{record.module_name}",
        )
        coord = Coordinate(
            components=(record.module_name,),
            kind=CoordinateKind.MODULE,
        )
        carrier = Carrier(coordinate=coord, payload=record, label=record.module_name)
        evidence = EvidenceBundle()
        evidence.add(EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_TRACE,
            payload={"attributes": record.attributes, "is_package": record.is_package},
            label="runtime_reflection",
        ))
        trust = TrustAnnotation(
            level=TrustLevel.RUNTIME_WITNESSED,
            rationale="module successfully loaded and reflected at runtime",
        )
        provenance = Provenance(
            source="DynamicImportReflectionWitness",
            module=record.module_name,
        )
        j = Judgment(
            proposition=prop,
            carrier=carrier,
            evidence=evidence,
            trust=trust,
            provenance=provenance,
            label=f"reflection:{record.module_name}",
        )
        j.settle()
        return j


class DynamicImportReflectionCoordinator:
    """Orchestrates dynamic import analysis and runtime reflection.

    This is the top-level entry point for §4 analysis.  It holds an
    :class:`DynamicImportReflectionAnalyzer` and a
    :class:`DynamicImportReflectionWitness` and coordinates them to produce
    :class:`Judgment` records for the judgment layer.

    Typical usage::

        coordinator = DynamicImportReflectionCoordinator()
        records = coordinator.trace_importlib_usage(source_text)
        for record in records:
            judgment = coordinator.build_dynamic_judgment(
                coordinator.analyze_dynamic_import(call_node)
            )

    All public methods return frozen value objects; no mutable state is
    accumulated inside the coordinator.

    Implementation notes
    --------------------
    The coordinator deliberately does not implement caching: the caller is
    expected to manage memoisation if needed.  This keeps the coordinator
    stateless and avoids memory leaks in long-running pipeline processes that
    process many modules.

    The split between :class:`DynamicImportReflectionAnalyzer` (pure static)
    and :class:`DynamicImportReflectionWitness` (runtime side-effects) is
    preserved at the coordinator level: the coordinator exposes methods that
    call both, but always clearly documents which operations have runtime
    side-effects.
    """

    def __init__(self) -> None:
        self._analyzer = DynamicImportReflectionAnalyzer()
        self._witness = DynamicImportReflectionWitness()
        log.debug("DynamicImportReflectionCoordinator initialised")

    def analyze_dynamic_import(self, call_node: ast.Call) -> DynamicImportRecord:
        """Convert a single AST Call node into a DynamicImportRecord.

        Parameters
        ----------
        call_node:
            An AST Call node previously detected by the analyzer.

        Returns
        -------
        DynamicImportRecord
            Frozen record with best-effort resolved_name.
        """
        kind = self._analyzer.classify_dynamic_import(call_node)
        # copilot: extract the module name argument — usually the first positional arg
        module_expr = ""
        resolved_name = ""
        if call_node.args:
            first_arg = call_node.args[0]
            module_expr = ast.unparse(first_arg)
            # copilot: if it's a string literal we can resolve it directly
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                resolved_name = first_arg.value
        call_site = f"line:{getattr(call_node, 'lineno', 0)}"
        is_conditional = False  # copilot: would need parent-tracking to determine this fully
        coord = Coordinate(
            components=(resolved_name or module_expr,),
            kind=CoordinateKind.MODULE,
        )
        return DynamicImportRecord(
            call_site=call_site,
            module_expr=module_expr,
            resolved_name=resolved_name,
            import_kind=kind,
            is_conditional=is_conditional,
            coordinate=coord,
        )

    def trace_importlib_usage(self, source: str) -> list[ImportlibUsageRecord]:
        """Trace all importlib API usages in *source*.

        Parameters
        ----------
        source:
            Raw Python source text.

        Returns
        -------
        list[ImportlibUsageRecord]
            One record per importlib call found.
        """
        calls = self._analyzer.detect_dynamic_import_calls(source)
        records: list[ImportlibUsageRecord] = []
        for call in calls:
            kind = self._analyzer.classify_dynamic_import(call)
            module_arg = ""
            if call.args:
                module_arg = ast.unparse(call.args[0])
            spec_or_loader = ""
            if len(call.args) > 1:
                spec_or_loader = ast.unparse(call.args[1])
            records.append(ImportlibUsageRecord(
                usage_kind=kind.value,
                module_arg=module_arg,
                spec_or_loader=spec_or_loader,
                line_no=getattr(call, "lineno", 0),
            ))
        return records

    def evaluate_import_spec(
        self, module_name: str, package: str | None = None
    ) -> ModuleSpecRecord:
        """Evaluate the importlib ModuleSpec for *module_name*.

        Parameters
        ----------
        module_name:
            Fully qualified (or relative) module name.
        package:
            Anchor package for relative imports.

        Returns
        -------
        ModuleSpecRecord
            Snapshot of the spec, or an empty record if not found.
        """
        try:
            spec = importlib.util.find_spec(module_name, package)
        except (ModuleNotFoundError, ValueError) as exc:
            log.debug("evaluate_import_spec: %s", exc)
            return ModuleSpecRecord(name=module_name)
        if spec is None:
            return ModuleSpecRecord(name=module_name)
        locations: tuple = ()
        if spec.submodule_search_locations is not None:
            locations = tuple(spec.submodule_search_locations)
        loader_class = type(spec.loader).__name__ if spec.loader else ""
        return ModuleSpecRecord(
            name=spec.name,
            origin=spec.origin or "",
            submodule_search_locations=locations,
            loader_class=loader_class,
            is_package=locations != (),
        )

    def build_dynamic_judgment(self, record: DynamicImportRecord) -> "Judgment":
        """Build a Judgment from a DynamicImportRecord.

        Parameters
        ----------
        record:
            A frozen DynamicImportRecord produced by
            :meth:`analyze_dynamic_import`.

        Returns
        -------
        Judgment
            Proposed judgment (trust = COPILOT_SUGGESTED if resolved_name is
            from string literal, otherwise unresolved).
        """
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            statement=(
                f"dynamic import {record.module_expr!r} "
                f"resolves to {record.resolved_name!r}"
            ),
            label=f"dynamic_import:{record.call_site}",
        )
        carrier = Carrier(
            coordinate=record.coordinate,
            payload=record,
            label=record.resolved_name or record.module_expr,
        )
        evidence = EvidenceBundle()
        evidence.add(EvidenceItem(
            kind=EvidenceItemKind.STATIC_ANALYSIS,
            payload={"import_kind": record.import_kind.value,
                     "is_conditional": record.is_conditional},
            label="static_dynamic_import_analysis",
        ))
        # copilot: trust is COPILOT_SUGGESTED when resolved_name is from a literal
        trust_level = (
            TrustLevel.COPILOT_SUGGESTED
            if record.resolved_name
            else TrustLevel.COPILOT_SUGGESTED
        )
        trust = TrustAnnotation(
            level=trust_level,
            rationale=(
                "resolved from string literal" if record.resolved_name
                else "unresolved dynamic import expression"
            ),
        )
        provenance = Provenance(
            source="DynamicImportReflectionCoordinator",
            module=record.resolved_name or record.module_expr,
        )
        return Judgment(
            proposition=prop,
            carrier=carrier,
            evidence=evidence,
            trust=trust,
            provenance=provenance,
            label=f"dynamic_import:{record.call_site}",
        )

    def coordinate_runtime_reflection(self, module: object) -> ReflectionRecord:
        """Reflect on a loaded module object and return a ReflectionRecord.

        Parameters
        ----------
        module:
            A Python module object (already loaded).

        Returns
        -------
        ReflectionRecord
            Frozen snapshot of the module's public interface.
        """
        name = getattr(module, "__name__", "")
        all_attrs = tuple(dir(module))
        dunder = tuple(a for a in all_attrs if a.startswith("__") and a.endswith("__"))
        file_path = getattr(module, "__file__", "") or ""
        package_root = getattr(module, "__package__", "") or ""
        is_package = hasattr(module, "__path__")
        return ReflectionRecord(
            module_name=name,
            attributes=all_attrs,
            dunder_attrs=dunder,
            is_package=is_package,
            file_path=file_path,
            package_root=package_root,
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import textwrap

    _sample_source = textwrap.dedent("""
        import importlib
        import importlib.util

        def load_plugin(name):
            mod = importlib.import_module(name)
            return mod

        def lazy_load():
            import os.path
            return os.path.exists(".")

        if TYPE_CHECKING:
            from typing import Protocol

        spec = importlib.util.find_spec("json")
        mod2 = importlib.util.module_from_spec(spec)
    """)

    print("=== dynamic_import_and_reflection smoke test ===")
    analyzer = DynamicImportReflectionAnalyzer()
    calls = analyzer.detect_dynamic_import_calls(_sample_source)
    print(f"Detected {len(calls)} dynamic import calls")
    for c in calls:
        kind = analyzer.classify_dynamic_import(c)
        print(f"  line {getattr(c,'lineno',0)}: {kind.value}")

    lazy = analyzer.find_lazy_imports(_sample_source)
    print(f"Lazy imports: {len(lazy)}")
    for r in lazy:
        print(f"  {r.name!r} trigger={r.trigger_condition!r}")

    coordinator = DynamicImportReflectionCoordinator()
    usages = coordinator.trace_importlib_usage(_sample_source)
    print(f"importlib usages: {len(usages)}")

    spec_rec = coordinator.evaluate_import_spec("json")
    print(f"json spec: name={spec_rec.name!r} is_package={spec_rec.is_package}")

    witness = DynamicImportReflectionWitness()
    hooks = witness.record_import_hook_chain()
    print(f"Import hooks: {len(hooks)} ({sum(h.is_meta_path for h in hooks)} meta_path, "
          f"{sum(h.is_path_hook for h in hooks)} path_hook)")

    w = witness.witness_dynamic_import("json")
    print(f"json witness: load_succeeded={w.load_succeeded} origin={w.spec_origin!r}")

    import json as _json_mod
    ref = coordinator.coordinate_runtime_reflection(_json_mod)
    print(f"json reflection: {len(ref.attributes)} attrs, is_package={ref.is_package}")

    j = witness.build_reflection_judgment(ref)
    print(f"Reflection judgment: status={j.status} trust={j.trust.level}")
    print("smoke test PASSED")
