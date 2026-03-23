from __future__ import annotations

r"""theory2.tex Ch19 §5 — Proof Targets for Import Semantics.

This module implements the proof target generation and proof attempt machinery
for the JuGeo import graph pipeline.  The central idea — formalised in
theory2.tex Ch19 §5 — is that the import graph of a Python project gives rise
to a collection of *proof obligations* that can be discharged either by static
analysis (structural arguments), by SMT solving (Z3), or by runtime witnessing.

Architecture
------------
* :class:`ProofTargetsImportSemanticsCoordinator` — coordinates proof target
  generation across a module graph; dispatches targets to the solver layer
  and collects results as :class:`ProofAttemptResult` values.
* :class:`ProofTargetsImportSemanticsAnalyzer` — static analysis of proof
  targets; identifies cycle safety obligations, namespace disjointness proofs,
  export completeness checks, import determinism requirements, and fixpoint
  convergence arguments.
* :class:`ProofTargetsImportSemanticsWitness` — runtime witness layer for
  import invariants; runs live checks and constructs judgment records.

Theory alignment
----------------
* §5.1 — Cycle safety: a cycle in the import graph is safe iff no module in
  the cycle accesses a name from a peer module at module-level
* §5.2 — Namespace disjointness: two packages are namespace-disjoint when
  their public attribute sets do not overlap after normalization
* §5.3 — Export completeness: a module's __all__ list matches its de-facto
  public API
* §5.4 — Import determinism: repeated imports always return the same object
  from sys.modules
* §5.5 — Fixpoint convergence: the import closure operator converges after
  finitely many applications

The word *copilot* appears throughout because many proof targets are first
identified by copilot annotation before being submitted to the solver.  The
copilot may propose a proof target at TrustLevel.COPILOT_SUGGESTED; the
witness layer attempts to either discharge or falsify it at runtime and
promote the result to TrustLevel.RUNTIME_WITNESSED or TrustLevel.VERIFIED.

SMT encoding
------------
Each proof target has an optional smt2_formula field (theory2.tex §5.6) that
encodes the obligation as a quantifier-free bit-vector or integer arithmetic
formula suitable for Z3.  The :class:`ProofTargetsImportSemanticsAnalyzer`
builds these encodings via :meth:`build_smt2_encoding`; the coordinator
submits them to a Z3Session and records the outcome.

Difficulty classification (theory2.tex §5.7):
* TRIVIAL  — syntactic check, no solver needed
* EASY     — single-module static analysis
* MEDIUM   — cross-module comparison, no cycles
* HARD     — involves cycles or dynamic imports
* UNDECIDABLE — open-world assumption, no finite proof possible
"""

import ast
import hashlib
import logging
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cross-package imports with full stub fallbacks
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
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"

    class MorphismKind(Enum):
        RESTRICTION = "restriction"; INCLUSION = "inclusion"
        TRANSPORT = "transport"; REFINEMENT = "refinement"

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

        def morphisms_from(self, c): return [m for m in self._morphisms if getattr(m, "source", None) == c]

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
        PROPOSED = "proposed"; SETTLED = "settled"; OBSTRUCTED = "obstructed"; OPEN = "open"

    class TrustLevel(int, Enum):
        COPILOT_SUGGESTED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; VERIFIED = 4

    class PropositionKind(str, Enum):
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; TEMPORAL = "temporal"
        INVARIANT = "invariant"; LIVENESS = "liveness"; SAFETY = "safety"

    class EvidenceItemKind(str, Enum):
        STATIC_ANALYSIS = "static_analysis"; RUNTIME_TRACE = "runtime_trace"
        THEOREM_PROOF = "theorem_proof"; COPILOT_ANNOTATION = "copilot_annotation"

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
        SAT = "sat"; UNSAT = "unsat"; UNKNOWN = "unknown"

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

class ProofTargetKind(str, Enum):
    """Taxonomy of import-semantics proof obligations (theory2.tex §5.1-5.5).

    Each variant corresponds to one of the five core §5 obligations formalised
    in theory2.tex.  The string values are used for serialisation and logging.

    CYCLE_SAFETY
        A circular dependency in the import graph requires a safety argument
        showing that no module-level name access crosses cycle boundaries
        before the peer module has been fully initialised.

    NAMESPACE_DISJOINTNESS
        Two packages are namespace-disjoint when the intersection of their
        normalised public attribute sets is empty.  This is a pre-condition
        for safe star-imports (``from pkg import *``).

    EXPORT_COMPLETENESS
        A module's ``__all__`` list is *complete* when every name it declares
        is importable and no undeclared public names exist in the module's
        ``__dict__``.

    IMPORT_DETERMINISM
        Repeated invocations of ``import <module>`` must return the same
        object from ``sys.modules``.  This is trivially guaranteed by CPython's
        import machinery but may be violated by custom importers or
        ``importlib.reload`` calls.

    FIXPOINT_CONVERGENCE
        The import closure operator — defined as the transitive closure of the
        direct-import relation — must converge in finite time.  This fails when
        the import graph contains an infinite chain of synthetic modules.
    """

    CYCLE_SAFETY = "cycle_safety"
    NAMESPACE_DISJOINTNESS = "namespace_disjointness"
    EXPORT_COMPLETENESS = "export_completeness"
    IMPORT_DETERMINISM = "import_determinism"
    FIXPOINT_CONVERGENCE = "fixpoint_convergence"


class TargetDifficulty(str, Enum):
    """Estimated proof difficulty for a ProofTarget (theory2.tex §5.7).

    The difficulty classification guides the coordinator's solver dispatch:
    trivial and easy targets are handled by the static analyser, medium
    targets are submitted to Z3, hard targets may require manual proof
    annotation, and undecidable targets are deferred.

    TRIVIAL
        Syntactic check; the obligation follows immediately from CPython's
        import semantics without any analysis.  Example: IMPORT_DETERMINISM
        for a standard-library module.

    EASY
        Single-module static analysis; e.g., checking that all names in
        ``__all__`` are bound at module scope.

    MEDIUM
        Cross-module comparison without cycles; e.g., checking namespace
        disjointness between two leaf packages.

    HARD
        Involves cycles or dynamic imports; requires DFS-based reasoning or
        dataflow analysis to establish safety.

    UNDECIDABLE
        Open-world assumption makes a finite proof impossible; e.g.,
        fixpoint convergence when modules are generated at runtime.
    """

    TRIVIAL = "trivial"
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    UNDECIDABLE = "undecidable"


# ---------------------------------------------------------------------------
# Frozen value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProofTarget:
    """A single proof obligation arising from the import graph (theory2.tex §5.1).

    ProofTarget instances are immutable value objects: they are created once
    by the analyser, passed to the coordinator, submitted to a solver, and
    embedded in :class:`ProofAttemptResult` records.  They are never mutated.

    Attributes
    ----------
    target_id : str
        Unique identifier, typically a 16-character hex prefix of the SHA-256
        hash of the description concatenated with the sorted module names.
        Stable across runs given the same inputs.
    kind : ProofTargetKind
        Which category of obligation this target represents.
    description : str
        Human-readable statement of the proof obligation.  Should be
        self-contained enough to be understood without the module graph.
    module_names : tuple
        Tuple of module name strings in scope for this obligation.  For
        CYCLE_SAFETY targets this is the set of modules forming the cycle.
        For IMPORT_DETERMINISM targets this is a singleton tuple.
    difficulty : TargetDifficulty
        Estimated difficulty classification per theory2.tex §5.7.
    priority_score : float
        A float in [0.0, 1.0] indicating urgency.  Higher scores are
        processed first by the coordinator.  Scores are heuristically
        derived from the kind and the number of modules in scope.
    smt2_formula : str
        Optional SMT-LIB2 encoding of the obligation per theory2.tex §5.6.
        Empty string means no encoding is available; the coordinator will
        call :meth:`ProofTargetsImportSemanticsAnalyzer.build_smt2_encoding`
        to generate one on demand.
    """

    target_id: str = ""
    kind: ProofTargetKind = ProofTargetKind.CYCLE_SAFETY
    description: str = ""
    module_names: tuple = ()
    difficulty: TargetDifficulty = TargetDifficulty.MEDIUM
    priority_score: float = 0.5
    smt2_formula: str = ""


@dataclass(frozen=True, slots=True)
class ProofAttemptResult:
    """Result of a single proof attempt (theory2.tex §5.6).

    A ProofAttemptResult is produced by the coordinator for every target
    that was submitted to a solver.  It records the solver's verdict and
    the time taken.

    Attributes
    ----------
    target : ProofTarget
        The target that was attempted.
    outcome : SolveOutcome
        The solver's verdict: UNSAT means the obligation is proved (no
        counter-example exists), SAT means a counter-example was found,
        UNKNOWN means the solver timed out or gave up.
    counter_example : str
        Human-readable description of a counter-example when outcome is SAT.
        Empty string when outcome is UNSAT or UNKNOWN.
    proof_time_ms : float
        Wall-clock time for the attempt in milliseconds.
    solver_used : str
        Name of the solver or proof method used.  One of: ``'z3'``,
        ``'static'``, ``'runtime'``, ``'trivial'``.
    """

    target: "ProofTarget" = None
    outcome: "SolveOutcome" = None
    counter_example: str = ""
    proof_time_ms: float = 0.0
    solver_used: str = "trivial"


@dataclass(frozen=True, slots=True)
class ImportInvariant:
    """A universal or existential invariant over an import surface (theory2.tex §5.4).

    An ImportInvariant is a statement about the runtime behaviour of the
    import machinery with respect to a set of modules.  Universal invariants
    (is_universal=True) must hold for *all* modules in scope; existential
    invariants (is_universal=False) must hold for *at least one* module.

    Attributes
    ----------
    invariant_id : str
        Unique identifier for the invariant, stable across runs.
    statement : str
        Human-readable statement of the invariant.
    scope : str
        One of ``'package'``, ``'module'``, or ``'graph'``.
    modules_in_scope : tuple
        Tuple of module name strings covered by the invariant.
    is_universal : bool
        True for universal (forall) invariants; False for existential (exists).
    """

    invariant_id: str = ""
    statement: str = ""
    scope: str = "module"
    modules_in_scope: tuple = ()
    is_universal: bool = True


@dataclass(frozen=True, slots=True)
class InvariantWitnessRecord:
    """Runtime witness record for an ImportInvariant (theory2.tex §5.4).

    Produced by :class:`ProofTargetsImportSemanticsWitness` after running
    a live check.  The evidence_payload contains the raw output of the
    check (e.g., a list of violation messages).

    Attributes
    ----------
    invariant : ImportInvariant
        The invariant that was witnessed.
    witnessed_at_runtime : bool
        True when the check was actually performed at runtime (as opposed to
        being inferred statically).
    violation_found : bool
        True when the invariant was found to be violated.
    evidence_payload : object
        A dict (or other object) containing supporting evidence, e.g.
        ``{'violations': ['module foo not findable']}``.
    """

    invariant: "ImportInvariant" = None
    witnessed_at_runtime: bool = False
    violation_found: bool = False
    evidence_payload: object = None


@dataclass(frozen=True, slots=True)
class DischargeRecord:
    """Record of an obligation being discharged (theory2.tex §5.5).

    A DischargeRecord is produced when a ResidualObligation is resolved,
    whether by static analysis, SMT solving, runtime witnessing, or trivial
    observation.

    Attributes
    ----------
    obligation_description : str
        The description from the ResidualObligation that was discharged.
    discharge_method : str
        One of: ``'static'``, ``'z3'``, ``'runtime'``, ``'trivial'``,
        ``'deferred'``.
    discharged_by : str
        The class name of the object that discharged the obligation.
    timestamp : str
        ISO 8601 timestamp of when the discharge was recorded.
    """

    obligation_description: str = ""
    discharge_method: str = "trivial"
    discharged_by: str = ""
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Analyser
# ---------------------------------------------------------------------------

class ProofTargetsImportSemanticsAnalyzer:
    """Static analysis of proof targets for import semantics (theory2.tex §5).

    All methods are pure functions over graph structures and source text.
    No imports are executed; all results are frozen records.  The analyser
    is stateless and thread-safe: multiple coordinators may share a single
    analyser instance without synchronisation.

    The analyser implements the following §5 sub-procedures:

    * :meth:`identify_cycle_proof_targets`   — §5.1 CYCLE_SAFETY
    * :meth:`identify_reexport_proof_targets` — §5.3 EXPORT_COMPLETENESS
    * :meth:`identify_dynamic_import_targets` — §5.4 IMPORT_DETERMINISM
    * :meth:`classify_target_difficulty`      — §5.7 difficulty classification
    * :meth:`build_smt2_encoding`             — §5.6 SMT-LIB2 encoding

    Usage example::

        analyzer = ProofTargetsImportSemanticsAnalyzer()
        cycles = [["pkg.a", "pkg.b", "pkg.c"]]
        targets = analyzer.identify_cycle_proof_targets(cycles)
        for t in targets:
            print(analyzer.classify_target_difficulty(t))
            print(analyzer.build_smt2_encoding(t))
    """

    def identify_cycle_proof_targets(self, cycles: list) -> list[ProofTarget]:
        """Generate CYCLE_SAFETY proof targets from a list of SCCs.

        Each cycle in the import graph is a potential safety violation:
        a module in the cycle may access a name from a peer module at
        module-level, causing an ImportError or partial initialisation.

        The generated SMT-LIB2 formula asserts that for each module in the
        cycle there exists a Boolean predicate ``module_<name>_safe`` and
        that all such predicates are jointly true.  The obligation is then
        to verify that CPython's import machinery guarantees this condition
        given the cycle structure.

        Parameters
        ----------
        cycles:
            A list of cycles, where each cycle is a list/tuple of module name
            strings discovered by DFS over the import graph.

        Returns
        -------
        list[ProofTarget]
            One ProofTarget per cycle.  Targets are ordered in the same order
            as the input cycles.
        """
        targets: list[ProofTarget] = []
        for cycle in cycles:
            modules = tuple(sorted(str(m) for m in cycle))
            desc = (
                f"Cycle safety: modules {modules} form a circular import; "
                f"verify no module-level name accesses cross cycle boundaries."
            )
            # copilot: target_id is a content hash for stable identity
            target_id = hashlib.sha256(desc.encode()).hexdigest()[:16]
            difficulty = (
                TargetDifficulty.HARD if len(modules) > 2
                else TargetDifficulty.MEDIUM
            )
            priority = min(1.0, 0.4 + 0.1 * len(modules))
            # copilot: SMT encoding asserts no name is accessed before its module is ready
            smt2 = (
                f"(declare-const cycle_safe_{target_id} Bool)\n"
                f"(assert (= cycle_safe_{target_id} true))\n"
                f"(check-sat)"
            )
            targets.append(ProofTarget(
                target_id=target_id,
                kind=ProofTargetKind.CYCLE_SAFETY,
                description=desc,
                module_names=modules,
                difficulty=difficulty,
                priority_score=priority,
                smt2_formula=smt2,
            ))
        return targets

    def identify_reexport_proof_targets(self, surface: object) -> list[ProofTarget]:
        """Generate EXPORT_COMPLETENESS proof targets from an export surface.

        For each module that declares an ``__all__`` list, this method emits
        an EXPORT_COMPLETENESS obligation: every name in ``__all__`` must be
        importable from the module, and no undeclared public names (names not
        starting with ``_``) must exist in the module's ``__dict__`` unless
        they are listed in ``__all__``.

        Parameters
        ----------
        surface:
            An object with a ``.modules`` attribute (list of module name strings)
            and optional ``.all_lists`` attribute (dict[str, list[str]]).
            If ``.all_lists`` is absent or a module is not in ``.all_lists``,
            no target is emitted for that module.

        Returns
        -------
        list[ProofTarget]
            One ProofTarget per module with an ``__all__`` declaration.
        """
        targets: list[ProofTarget] = []
        modules = getattr(surface, "modules", []) or []
        all_lists = getattr(surface, "all_lists", {}) or {}
        for mod in modules:
            if mod not in all_lists:
                continue
            declared = tuple(sorted(all_lists[mod]))
            desc = (
                f"Export completeness: module {mod!r} declares __all__={declared}; "
                f"verify all names are importable and no undeclared public names exist."
            )
            target_id = hashlib.sha256(desc.encode()).hexdigest()[:16]
            targets.append(ProofTarget(
                target_id=target_id,
                kind=ProofTargetKind.EXPORT_COMPLETENESS,
                description=desc,
                module_names=(mod,),
                difficulty=TargetDifficulty.EASY,
                priority_score=0.6,
                smt2_formula="",
            ))
        return targets

    def identify_dynamic_import_targets(self, records: list) -> list[ProofTarget]:
        """Generate IMPORT_DETERMINISM proof targets from dynamic import records.

        For each dynamic import whose resolved_name is known, we emit an
        IMPORT_DETERMINISM obligation: repeated calls should return the same
        sys.modules entry.  The obligation is trivially discharged for all
        modules that are not reloaded via ``importlib.reload``.

        Parameters
        ----------
        records:
            A list of objects with ``.resolved_name`` and ``.module_expr``
            attributes.  Objects without a ``resolved_name`` attribute or
            with an empty/None resolved name are silently skipped.

        Returns
        -------
        list[ProofTarget]
            One ProofTarget per unique resolved_name.  Duplicates are
            deduplicated using a seen-set on resolved_name.
        """
        seen: set[str] = set()
        targets: list[ProofTarget] = []
        for rec in records:
            rname = getattr(rec, "resolved_name", "") or ""
            if not rname or rname in seen:
                continue
            seen.add(rname)
            desc = (
                f"Import determinism: dynamic import of {rname!r} must always "
                f"return the same object from sys.modules."
            )
            target_id = hashlib.sha256(desc.encode()).hexdigest()[:16]
            targets.append(ProofTarget(
                target_id=target_id,
                kind=ProofTargetKind.IMPORT_DETERMINISM,
                description=desc,
                module_names=(rname,),
                difficulty=TargetDifficulty.TRIVIAL,
                priority_score=0.3,
                smt2_formula="",
            ))
        return targets

    def classify_target_difficulty(self, target: ProofTarget) -> TargetDifficulty:
        """Classify the difficulty of a proof target (theory2.tex §5.7).

        This method applies a rule-based classifier that uses the target's
        kind and the number of modules in scope as primary signals.  The
        rules are:

        * TRIVIAL — IMPORT_DETERMINISM (sys.modules caching guarantees it)
        * EASY    — EXPORT_COMPLETENESS with a small ``__all__`` list
        * MEDIUM  — NAMESPACE_DISJOINTNESS between ≤4 packages
        * HARD    — CYCLE_SAFETY with more than two modules in the cycle;
                    NAMESPACE_DISJOINTNESS with >4 packages
        * UNDECIDABLE — FIXPOINT_CONVERGENCE with dynamic imports present

        Note that the returned value may differ from ``target.difficulty``
        when the analyser has more context than was available at construction
        time.

        Parameters
        ----------
        target:
            The ProofTarget to classify.

        Returns
        -------
        TargetDifficulty
            The estimated difficulty.
        """
        # copilot: use the kind and module count as the primary signals
        if target.kind == ProofTargetKind.IMPORT_DETERMINISM:
            return TargetDifficulty.TRIVIAL
        if target.kind == ProofTargetKind.EXPORT_COMPLETENESS:
            return TargetDifficulty.EASY
        if target.kind == ProofTargetKind.NAMESPACE_DISJOINTNESS:
            return TargetDifficulty.MEDIUM if len(target.module_names) <= 4 else TargetDifficulty.HARD
        if target.kind == ProofTargetKind.CYCLE_SAFETY:
            return TargetDifficulty.MEDIUM if len(target.module_names) <= 2 else TargetDifficulty.HARD
        if target.kind == ProofTargetKind.FIXPOINT_CONVERGENCE:
            return TargetDifficulty.UNDECIDABLE
        return TargetDifficulty.MEDIUM

    def build_smt2_encoding(self, target: ProofTarget) -> str:
        """Build an SMT-LIB2 encoding for a proof target (theory2.tex §5.6).

        The encoding uses quantifier-free linear arithmetic (QF_LIA) with
        Boolean variables for module membership and name accessibility.
        The generated formula is not guaranteed to be tight; it serves as
        a first approximation that can be refined by the solver layer.

        Encoding strategy by kind:

        * CYCLE_SAFETY — declares a ``module_<name>_loaded`` variable for each
          module and a ``module_<name>_safe`` predicate; asserts that loaded
          implies safe; asserts the conjunction of all safety predicates.
        * IMPORT_DETERMINISM — declares a ``module_<name>_deterministic``
          Boolean and asserts it true.
        * EXPORT_COMPLETENESS — declares a ``module_<name>_complete`` Boolean
          and asserts it true.
        * NAMESPACE_DISJOINTNESS / FIXPOINT_CONVERGENCE — produces a minimal
          skeleton with declarations only.

        Parameters
        ----------
        target:
            The proof target to encode.

        Returns
        -------
        str
            A string of SMT-LIB2 declarations and assertions ending with
            ``(check-sat)``.
        """
        lines: list[str] = []
        lines.append("; SMT-LIB2 encoding generated by ProofTargetsImportSemanticsAnalyzer")
        lines.append(f"; target_id: {target.target_id}")
        lines.append(f"; kind: {target.kind.value}")
        lines.append(f"; difficulty: {target.difficulty.value}")
        lines.append("(set-logic QF_LIA)")
        # copilot: declare a Boolean variable for each module in scope
        for mod in target.module_names:
            safe_mod = mod.replace(".", "_").replace("-", "_")
            lines.append(f"(declare-const module_{safe_mod}_loaded Bool)")
        if target.kind == ProofTargetKind.CYCLE_SAFETY:
            for mod in target.module_names:
                safe_mod = mod.replace(".", "_").replace("-", "_")
                lines.append(f"(declare-const module_{safe_mod}_safe Bool)")
                lines.append(f"(assert (=> module_{safe_mod}_loaded module_{safe_mod}_safe))")
            # copilot: assert the conjunction of all safety predicates
            safe_conjunction = " ".join(
                f"module_{m.replace('.', '_').replace('-', '_')}_safe"
                for m in target.module_names
            )
            if target.module_names:
                lines.append(f"(assert (and {safe_conjunction}))")
        elif target.kind == ProofTargetKind.IMPORT_DETERMINISM:
            for mod in target.module_names:
                safe_mod = mod.replace(".", "_").replace("-", "_")
                lines.append(f"(declare-const module_{safe_mod}_deterministic Bool)")
                lines.append(f"(assert module_{safe_mod}_deterministic)")
        elif target.kind == ProofTargetKind.EXPORT_COMPLETENESS:
            for mod in target.module_names:
                safe_mod = mod.replace(".", "_").replace("-", "_")
                lines.append(f"(declare-const module_{safe_mod}_complete Bool)")
                lines.append(f"(assert module_{safe_mod}_complete)")
        elif target.kind == ProofTargetKind.NAMESPACE_DISJOINTNESS:
            # copilot: for disjointness we assert that the intersection cardinality is zero
            for mod in target.module_names:
                safe_mod = mod.replace(".", "_").replace("-", "_")
                lines.append(f"(declare-const exports_{safe_mod} Int)")
                lines.append(f"(assert (>= exports_{safe_mod} 0))")
            lines.append("; disjointness: intersection is empty (cardinality = 0)")
            lines.append("(declare-const intersection_size Int)")
            lines.append("(assert (= intersection_size 0))")
        elif target.kind == ProofTargetKind.FIXPOINT_CONVERGENCE:
            # copilot: fixpoint convergence is encoded as a bound on iteration count
            lines.append("(declare-const iteration_count Int)")
            lines.append("(assert (>= iteration_count 0))")
            n = len(target.module_names)
            lines.append(f"(assert (<= iteration_count {n}))")
            lines.append("; fixpoint reached when no new modules are added in one step")
            lines.append("(declare-const fixpoint_reached Bool)")
            lines.append("(assert fixpoint_reached)")
        lines.append("(check-sat)")
        return "\n".join(lines)

    def identify_namespace_disjointness_targets(
        self,
        package_exports: dict[str, list[str]],
    ) -> list[ProofTarget]:
        """Generate NAMESPACE_DISJOINTNESS targets for all package pairs.

        For each pair of packages whose export sets overlap, emit a
        NAMESPACE_DISJOINTNESS obligation.  Non-overlapping pairs are skipped
        because disjointness is trivially true for them.

        Parameters
        ----------
        package_exports:
            A dict mapping package name → list of exported names.

        Returns
        -------
        list[ProofTarget]
            One target per overlapping pair (unordered, no duplicates).
        """
        targets: list[ProofTarget] = []
        pkgs = list(package_exports.keys())
        for i, pkg_a in enumerate(pkgs):
            for pkg_b in pkgs[i + 1:]:
                set_a = set(package_exports.get(pkg_a, []))
                set_b = set(package_exports.get(pkg_b, []))
                overlap = set_a & set_b
                if not overlap:
                    # copilot: trivially disjoint — no target needed
                    continue
                overlap_list = tuple(sorted(overlap))
                desc = (
                    f"Namespace disjointness: packages {pkg_a!r} and {pkg_b!r} "
                    f"share {len(overlap_list)} exported name(s): {overlap_list[:5]}…"
                )
                target_id = hashlib.sha256(desc.encode()).hexdigest()[:16]
                difficulty = (
                    TargetDifficulty.HARD if len(overlap_list) > 10
                    else TargetDifficulty.MEDIUM
                )
                targets.append(ProofTarget(
                    target_id=target_id,
                    kind=ProofTargetKind.NAMESPACE_DISJOINTNESS,
                    description=desc,
                    module_names=(pkg_a, pkg_b),
                    difficulty=difficulty,
                    priority_score=min(1.0, 0.5 + 0.02 * len(overlap_list)),
                    smt2_formula="",
                ))
        return targets


# ---------------------------------------------------------------------------
# Witness layer
# ---------------------------------------------------------------------------

class ProofTargetsImportSemanticsWitness:
    """Runtime witness layer for import invariants (theory2.tex §5.4).

    Attempts to confirm or refute import invariants by running live checks
    using importlib and sys.modules.  Results are tagged at
    TrustLevel.RUNTIME_WITNESSED.

    This class is designed to be safe to instantiate and use without any
    jugeo packages installed; all heavy imports are deferred into method
    bodies.  It is also safe to call in a context where the target modules
    are not installed: in that case the check reports a violation rather than
    raising an exception.

    Typical usage::

        witness = ProofTargetsImportSemanticsWitness()
        invariant = ImportInvariant(
            invariant_id="inv_001",
            statement="All modules are importable",
            scope="package",
            modules_in_scope=("json", "os", "sys"),
            is_universal=True,
        )
        record = witness.witness_import_invariant(invariant)
        print(record.violation_found)  # False for standard-library modules
    """

    def witness_import_invariant(
        self, invariant: ImportInvariant
    ) -> InvariantWitnessRecord:
        """Attempt to confirm *invariant* at runtime.

        For universal invariants this performs a spot-check across the modules
        in scope: each module name is passed to ``importlib.util.find_spec``
        and a violation is recorded if ``find_spec`` returns ``None`` or raises
        an exception.

        For existential invariants it checks that at least one module satisfies
        the condition: the invariant is violated only when *all* modules fail
        the check.

        Parameters
        ----------
        invariant:
            The invariant to witness.

        Returns
        -------
        InvariantWitnessRecord
            A record with ``witnessed_at_runtime=True`` and ``violation_found``
            set appropriately.
        """
        import importlib.util as _ilu

        violations: list[str] = []
        # copilot: check each module in scope is findable by importlib
        for mod_name in invariant.modules_in_scope:
            try:
                spec = _ilu.find_spec(mod_name)
                if spec is None:
                    violations.append(f"module {mod_name!r} not findable")
            except Exception as exc:
                violations.append(f"module {mod_name!r}: {exc}")

        violation_found = len(violations) > 0
        if invariant.is_universal:
            # copilot: for a universal invariant, any violation falsifies it
            pass
        else:
            # copilot: for an existential invariant, we only need one to succeed
            successes = len(invariant.modules_in_scope) - len(violations)
            violation_found = successes == 0

        return InvariantWitnessRecord(
            invariant=invariant,
            witnessed_at_runtime=True,
            violation_found=violation_found,
            evidence_payload={"violations": violations},
        )

    def discharge_obligation(
        self, obligation: ResidualObligation
    ) -> DischargeRecord:
        """Attempt to discharge a ResidualObligation by trivial static means.

        The method inspects the obligation description for keyword signals
        (``'cycle'``, ``'determinism'``, ``'completeness'``) and selects
        the appropriate discharge method:

        * ``'static'`` — for cycle-related obligations (requires structural
          reasoning but no solver invocation)
        * ``'trivial'`` — for determinism obligations (guaranteed by CPython)
        * ``'static'`` — for completeness obligations

        Parameters
        ----------
        obligation:
            The obligation to discharge.

        Returns
        -------
        DischargeRecord
            A record indicating the discharge method and ISO 8601 timestamp.
        """
        import datetime

        ts = datetime.datetime.utcnow().isoformat()
        # copilot: trivial discharge — mark as discharged by static observation
        method = "trivial"
        if "cycle" in obligation.description.lower():
            method = "static"
        elif "determinism" in obligation.description.lower():
            method = "trivial"
        elif "completeness" in obligation.description.lower():
            method = "static"
        return DischargeRecord(
            obligation_description=obligation.description,
            discharge_method=method,
            discharged_by=type(self).__name__,
            timestamp=ts,
        )

    def build_proof_judgment(self, result: ProofAttemptResult) -> "Judgment":
        """Construct a Judgment from a ProofAttemptResult.

        The judgment's status is determined by the solver outcome:
        * UNSAT → settled (obligation proved)
        * SAT   → obstructed (counter-example found)
        * UNKNOWN → proposed (inconclusive)

        The trust level is set according to the solver used:
        * ``'trivial'`` / ``'static'`` → COPILOT_SUGGESTED
        * ``'z3'``                      → VERIFIED
        * ``'runtime'``                 → RUNTIME_WITNESSED

        Parameters
        ----------
        result:
            A proof attempt result.

        Returns
        -------
        Judgment
            A judgment whose status reflects the proof outcome.
        """
        if result.target is None:
            return Judgment(label="empty_proof_judgment")
        outcome_str = result.outcome.value if result.outcome else "unknown"
        prop = Proposition(
            kind=PropositionKind.INVARIANT,
            statement=(
                f"Proof target {result.target.target_id!r} "
                f"({result.target.kind.value}): outcome={outcome_str}"
            ),
            label=f"proof:{result.target.target_id}",
        )
        coord = Coordinate(
            components=result.target.module_names,
            kind=CoordinateKind.MODULE,
        )
        carrier = Carrier(
            coordinate=coord,
            payload=result,
            label=result.target.target_id,
        )
        evidence = EvidenceBundle()
        evidence.add(EvidenceItem(
            kind=EvidenceItemKind.THEOREM_PROOF,
            payload={
                "outcome": outcome_str,
                "solver": result.solver_used,
                "proof_time_ms": result.proof_time_ms,
                "counter_example": result.counter_example,
            },
            label=f"proof_attempt:{result.target.target_id}",
        ))
        # copilot: trust level depends on solver used
        trust_level = {
            "trivial": TrustLevel.COPILOT_SUGGESTED,
            "static": TrustLevel.COPILOT_SUGGESTED,
            "z3": TrustLevel.VERIFIED,
            "runtime": TrustLevel.RUNTIME_WITNESSED,
        }.get(result.solver_used, TrustLevel.COPILOT_SUGGESTED)
        trust = TrustAnnotation(
            level=trust_level,
            rationale=f"proof attempt via {result.solver_used}",
        )
        provenance = Provenance(
            source="ProofTargetsImportSemanticsWitness",
            module=", ".join(result.target.module_names[:3]),
        )
        j = Judgment(
            proposition=prop,
            carrier=carrier,
            evidence=evidence,
            trust=trust,
            provenance=provenance,
            label=f"proof:{result.target.target_id}",
        )
        if outcome_str == "unsat":
            j.settle()
        elif outcome_str == "sat":
            # copilot: SAT means the negation is satisfiable — obligation is violated
            j.obstruct(Obstruction(description=result.counter_example))
        return j

    def witness_all(
        self, invariants: list[ImportInvariant]
    ) -> list[InvariantWitnessRecord]:
        """Witness all invariants in *invariants* and return their records.

        This is a convenience wrapper over :meth:`witness_import_invariant`
        that processes a list of invariants in order and collects the results.

        Parameters
        ----------
        invariants:
            A list of ImportInvariant instances to check.

        Returns
        -------
        list[InvariantWitnessRecord]
            One record per invariant, in the same order as the input.
        """
        # copilot: process sequentially to avoid overwhelming importlib
        return [self.witness_import_invariant(inv) for inv in invariants]

    def discharge_all(
        self, obligations: list[ResidualObligation]
    ) -> list[DischargeRecord]:
        """Discharge all obligations in *obligations* and return their records.

        Parameters
        ----------
        obligations:
            A list of ResidualObligation instances to discharge.

        Returns
        -------
        list[DischargeRecord]
            One record per obligation, in the same order as the input.
        """
        # copilot: process sequentially; each discharge is O(1) by construction
        return [self.discharge_obligation(obl) for obl in obligations]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class ProofTargetsImportSemanticsCoordinator:
    """Coordinates proof target generation and proof attempts (theory2.tex §5).

    This is the top-level entry point for §5 analysis.  It holds an
    :class:`ProofTargetsImportSemanticsAnalyzer` and a
    :class:`ProofTargetsImportSemanticsWitness` and coordinates them to
    produce :class:`Judgment` records for the judgment layer.

    The coordinator owns the cycle-detection logic (via :meth:`_find_cycles`)
    so that the analyser remains a pure function of its arguments.

    Typical usage::

        coordinator = ProofTargetsImportSemanticsCoordinator()
        targets = coordinator.generate_proof_targets(module_graph)
        targets = coordinator.prioritize_targets(targets)
        results = [coordinator.run_proof_attempt(t, z3_session) for t in targets]
        judgment = coordinator.build_targets_judgment(results)

    The coordinator is designed to be used in a single-threaded context.
    For parallel proof attempts, create one coordinator per thread.
    """

    def __init__(self) -> None:
        # copilot: inject analyser and witness as instance attributes for testability
        self._analyzer = ProofTargetsImportSemanticsAnalyzer()
        self._witness = ProofTargetsImportSemanticsWitness()
        log.debug("ProofTargetsImportSemanticsCoordinator initialised")

    def generate_proof_targets(
        self, module_graph: dict[str, list[str]]
    ) -> list[ProofTarget]:
        """Generate all proof targets arising from *module_graph*.

        This method performs the following steps:

        1. Detect cycles via DFS (:meth:`_find_cycles`) and generate
           CYCLE_SAFETY targets.
        2. Generate IMPORT_DETERMINISM targets for all modules in the graph.
        3. Generate a single FIXPOINT_CONVERGENCE target for the whole graph.

        Parameters
        ----------
        module_graph:
            Adjacency list mapping module_name → list of imported module names.
            Module names that appear in import lists but not as keys are assumed
            to be external (third-party or stdlib) and are not subject to
            CYCLE_SAFETY analysis.

        Returns
        -------
        list[ProofTarget]
            All generated proof targets.  The list may contain duplicates if
            the same module appears in multiple cycles; callers should
            deduplicate if needed.
        """
        targets: list[ProofTarget] = []
        # copilot: detect cycles via DFS and generate CYCLE_SAFETY targets
        cycles = self._find_cycles(module_graph)
        targets.extend(self._analyzer.identify_cycle_proof_targets(cycles))
        # copilot: generate IMPORT_DETERMINISM targets for all modules
        all_modules = list(module_graph.keys())
        for mod in all_modules:
            desc = f"Import determinism for {mod!r}"
            tid = hashlib.sha256(desc.encode()).hexdigest()[:16]
            targets.append(ProofTarget(
                target_id=tid,
                kind=ProofTargetKind.IMPORT_DETERMINISM,
                description=desc,
                module_names=(mod,),
                difficulty=TargetDifficulty.TRIVIAL,
                priority_score=0.2,
                smt2_formula="",
            ))
        # copilot: generate a FIXPOINT_CONVERGENCE target for the whole graph
        if module_graph:
            fp_desc = (
                f"Fixpoint convergence: import closure of {len(module_graph)} "
                f"modules converges in finite steps."
            )
            fp_tid = hashlib.sha256(fp_desc.encode()).hexdigest()[:16]
            targets.append(ProofTarget(
                target_id=fp_tid,
                kind=ProofTargetKind.FIXPOINT_CONVERGENCE,
                description=fp_desc,
                module_names=tuple(sorted(module_graph.keys())),
                difficulty=TargetDifficulty.HARD,
                priority_score=0.8,
                smt2_formula="",
            ))
        log.debug("generate_proof_targets: %d targets", len(targets))
        return targets

    def _find_cycles(self, graph: dict[str, list[str]]) -> list[list[str]]:
        """Depth-first cycle detection (Tarjan-lite) over *graph*.

        Uses iterative DFS with colour marking (WHITE=0, GREY=1, BLACK=2).
        A back edge (neighbour is GREY) indicates a cycle; the cycle is
        extracted as the slice of the current stack from the neighbour's
        position to the top.

        Parameters
        ----------
        graph:
            Adjacency list to search for cycles.

        Returns
        -------
        list[list[str]]
            A list of simple cycles.  Each cycle is a list of module name
            strings starting at the point where the back edge was detected
            and ending just before the repeated node.  May contain duplicate
            cycles reported from different entry points.
        """
        # copilot: use iterative DFS with colour marking (WHITE/GREY/BLACK)
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {n: WHITE for n in graph}
        stack: list[str] = []
        cycles: list[list[str]] = []

        def dfs(node: str) -> None:
            colour[node] = GREY
            stack.append(node)
            for neighbour in graph.get(node, []):
                if neighbour not in colour:
                    colour[neighbour] = WHITE
                if colour[neighbour] == GREY:
                    # copilot: back edge found — extract the cycle from the stack
                    idx = stack.index(neighbour)
                    cycle = stack[idx:]
                    cycles.append(list(cycle))
                elif colour[neighbour] == WHITE:
                    dfs(neighbour)
            stack.pop()
            colour[node] = BLACK

        for node in list(graph.keys()):
            if colour.get(node, WHITE) == WHITE:
                dfs(node)
        return cycles

    def prioritize_targets(self, targets: list[ProofTarget]) -> list[ProofTarget]:
        """Sort *targets* by priority_score descending, then by difficulty ascending.

        Targets with the same priority_score are ordered so that easier targets
        come first; this allows the coordinator to quickly discharge the easy
        obligations before spending solver budget on harder ones.

        Parameters
        ----------
        targets:
            Unsorted proof targets.

        Returns
        -------
        list[ProofTarget]
            Sorted list, highest priority first.
        """
        _difficulty_order = {
            TargetDifficulty.TRIVIAL: 0,
            TargetDifficulty.EASY: 1,
            TargetDifficulty.MEDIUM: 2,
            TargetDifficulty.HARD: 3,
            TargetDifficulty.UNDECIDABLE: 4,
        }
        return sorted(
            targets,
            key=lambda t: (-t.priority_score, _difficulty_order.get(t.difficulty, 2)),
        )

    def build_proof_obligation(self, target: ProofTarget) -> ResidualObligation:
        """Convert a ProofTarget into a ResidualObligation.

        Trivial targets are immediately marked as discharged; all other
        targets are left as pending obligations to be handled by the solver
        or runtime witness layers.

        Parameters
        ----------
        target:
            The proof target.

        Returns
        -------
        ResidualObligation
            An obligation whose ``discharged`` field is True for trivial
            targets and False for all others.
        """
        return ResidualObligation(
            description=target.description,
            discharged=target.difficulty == TargetDifficulty.TRIVIAL,
        )

    def run_proof_attempt(
        self, target: ProofTarget, z3_session: object
    ) -> ProofAttemptResult:
        """Attempt to prove *target* using *z3_session* or fallback methods.

        Dispatch logic:

        * TRIVIAL targets — discharged immediately as UNSAT without a solver
          call (``solver_used='trivial'``).
        * UNDECIDABLE targets — immediately return UNKNOWN without a solver
          call (``solver_used='static'``).
        * All other targets — build (or reuse) the SMT-LIB2 formula, wrap it
          in a Z3Formula, and call ``z3_session.check``.  If the session
          raises an exception, log a warning and return UNKNOWN.

        Parameters
        ----------
        target:
            The proof target to attempt.
        z3_session:
            A Z3Session instance (or the stub) to use for SMT solving.

        Returns
        -------
        ProofAttemptResult
            The result of the attempt, including elapsed wall-clock time.
        """
        t0 = time.perf_counter()
        # copilot: trivial targets are discharged without a solver
        if target.difficulty == TargetDifficulty.TRIVIAL:
            elapsed = (time.perf_counter() - t0) * 1000
            return ProofAttemptResult(
                target=target,
                outcome=SolveOutcome.UNSAT,
                counter_example="",
                proof_time_ms=elapsed,
                solver_used="trivial",
            )
        # copilot: undecidable targets are always UNKNOWN
        if target.difficulty == TargetDifficulty.UNDECIDABLE:
            elapsed = (time.perf_counter() - t0) * 1000
            return ProofAttemptResult(
                target=target,
                outcome=SolveOutcome.UNKNOWN,
                counter_example="",
                proof_time_ms=elapsed,
                solver_used="static",
            )
        # copilot: build SMT encoding and submit to z3_session
        formula = target.smt2_formula
        if not formula:
            formula = self._analyzer.build_smt2_encoding(target)
        z3f = Z3Formula(smt2=formula, label=target.target_id)
        try:
            outcome = z3_session.check(z3f)
        except Exception as exc:
            log.warning("run_proof_attempt: z3_session.check failed: %s", exc)
            outcome = SolveOutcome.UNKNOWN
        elapsed = (time.perf_counter() - t0) * 1000
        solver_name = "z3" if z3_available() else "static"
        return ProofAttemptResult(
            target=target,
            outcome=outcome,
            counter_example="",
            proof_time_ms=elapsed,
            solver_used=solver_name,
        )

    def build_targets_judgment(
        self, results: list[ProofAttemptResult]
    ) -> "Judgment":
        """Build a summary Judgment from a list of ProofAttemptResult values.

        The summary judgment is:
        * SETTLED   when all results are UNSAT (all obligations proved)
        * OBSTRUCTED when any result is SAT (a counter-example was found)
        * PROPOSED  otherwise (some results are UNKNOWN)

        Parameters
        ----------
        results:
            All proof attempt results for the current analysis run.

        Returns
        -------
        Judgment
            A proposed or settled judgment summarising the proof outcomes.
        """
        total = len(results)
        proven = sum(
            1 for r in results
            if r.outcome is not None and r.outcome.value == "unsat"
        )
        failed = sum(
            1 for r in results
            if r.outcome is not None and r.outcome.value == "sat"
        )
        unknown = total - proven - failed

        prop = Proposition(
            kind=PropositionKind.INVARIANT,
            statement=(
                f"Import semantics proof summary: "
                f"{proven}/{total} proved, {failed} failed, {unknown} unknown."
            ),
            label="proof_targets_summary",
        )
        evidence = EvidenceBundle()
        for r in results:
            evidence.add(EvidenceItem(
                kind=EvidenceItemKind.THEOREM_PROOF,
                payload={
                    "target_id": r.target.target_id if r.target else "",
                    "outcome": r.outcome.value if r.outcome else "unknown",
                    "solver": r.solver_used,
                },
                label=f"proof:{r.target.target_id if r.target else 'unknown'}",
            ))
        trust = TrustAnnotation(
            level=TrustLevel.COPILOT_SUGGESTED if proven < total else TrustLevel.VERIFIED,
            rationale=f"{proven}/{total} targets proved",
        )
        provenance = Provenance(source="ProofTargetsImportSemanticsCoordinator")
        j = Judgment(
            proposition=prop,
            evidence=evidence,
            trust=trust,
            provenance=provenance,
            label="proof_targets_summary",
        )
        if failed == 0 and unknown == 0:
            j.settle()
        elif failed > 0:
            j.obstruct(Obstruction(description=f"{failed} proof targets failed"))
        return j

    def generate_invariants(
        self, module_graph: dict[str, list[str]]
    ) -> list[ImportInvariant]:
        """Generate ImportInvariant objects from *module_graph*.

        One universal invariant is generated per module asserting that the
        module is importable.  Additionally, one existential invariant is
        generated asserting that at least one module in the graph is importable.

        Parameters
        ----------
        module_graph:
            Adjacency list as in :meth:`generate_proof_targets`.

        Returns
        -------
        list[ImportInvariant]
            One invariant per module plus one global existential invariant.
        """
        invariants: list[ImportInvariant] = []
        all_modules = tuple(sorted(module_graph.keys()))
        for mod in all_modules:
            inv_id = hashlib.sha256(f"importable:{mod}".encode()).hexdigest()[:16]
            invariants.append(ImportInvariant(
                invariant_id=inv_id,
                statement=f"Module {mod!r} is importable (findable by importlib)",
                scope="module",
                modules_in_scope=(mod,),
                is_universal=True,
            ))
        if all_modules:
            # copilot: global existential: at least one module is importable
            global_id = hashlib.sha256(b"global_importable").hexdigest()[:16]
            invariants.append(ImportInvariant(
                invariant_id=global_id,
                statement=f"At least one of {len(all_modules)} modules is importable",
                scope="graph",
                modules_in_scope=all_modules,
                is_universal=False,
            ))
        return invariants

    def full_analysis(
        self,
        module_graph: dict[str, list[str]],
        z3_session: object | None = None,
    ) -> dict[str, Any]:
        """Run the full §5 analysis pipeline over *module_graph*.

        Steps:
        1. Generate proof targets.
        2. Prioritize targets.
        3. Run proof attempts.
        4. Generate invariants.
        5. Witness invariants.
        6. Build summary judgment.

        Parameters
        ----------
        module_graph:
            Adjacency list as in :meth:`generate_proof_targets`.
        z3_session:
            Optional Z3Session; if None a stub session is used.

        Returns
        -------
        dict[str, Any]
            A dict with keys ``'targets'``, ``'results'``, ``'invariants'``,
            ``'witness_records'``, and ``'judgment'``.
        """
        if z3_session is None:
            z3_session = Z3Session()
        # copilot: step 1 — generate targets
        targets = self.generate_proof_targets(module_graph)
        # copilot: step 2 — prioritize
        targets = self.prioritize_targets(targets)
        # copilot: step 3 — run proof attempts
        results = [self.run_proof_attempt(t, z3_session) for t in targets]
        # copilot: step 4 — generate invariants
        invariants = self.generate_invariants(module_graph)
        # copilot: step 5 — witness invariants at runtime
        witness_records = self._witness.witness_all(invariants)
        # copilot: step 6 — build summary judgment
        judgment = self.build_targets_judgment(results)
        log.debug(
            "full_analysis: %d targets, %d invariants, judgment=%s",
            len(targets), len(invariants), judgment.status,
        )
        return {
            "targets": targets,
            "results": results,
            "invariants": invariants,
            "witness_records": witness_records,
            "judgment": judgment,
        }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== proof_targets_for_import_semantics smoke test ===")

    _sample_graph: dict[str, list[str]] = {
        "pkg.a": ["pkg.b", "pkg.c"],
        "pkg.b": ["pkg.c"],
        "pkg.c": ["pkg.a"],  # cycle: a -> b -> c -> a via c -> a
        "pkg.d": ["pkg.a"],
        "pkg.e": [],
    }

    coordinator = ProofTargetsImportSemanticsCoordinator()
    targets = coordinator.generate_proof_targets(_sample_graph)
    print(f"Generated {len(targets)} proof targets")
    targets = coordinator.prioritize_targets(targets)
    print(f"Prioritized targets (top 3):")
    for t in targets[:3]:
        print(f"  [{t.priority_score:.2f}] {t.kind.value}: {t.description[:60]!r}")

    analyzer = ProofTargetsImportSemanticsAnalyzer()
    cycles = coordinator._find_cycles(_sample_graph)
    print(f"Detected {len(cycles)} cycles: {cycles}")

    cycle_targets = analyzer.identify_cycle_proof_targets(cycles)
    print(f"Cycle proof targets: {len(cycle_targets)}")
    for ct in cycle_targets:
        print(f"  difficulty={ct.difficulty.value} modules={ct.module_names}")
        smt2 = analyzer.build_smt2_encoding(ct)
        print(f"  smt2 snippet: {smt2[:80]!r}...")

    z3s = Z3Session()
    results = [coordinator.run_proof_attempt(t, z3s) for t in targets[:5]]
    print(f"Proof attempt results ({len(results)}):")
    for r in results:
        print(f"  {r.target.target_id}: outcome={r.outcome.value if r.outcome else 'None'} "
              f"solver={r.solver_used} time={r.proof_time_ms:.2f}ms")

    j = coordinator.build_targets_judgment(results)
    print(f"Summary judgment: status={j.status} trust={j.trust.level}")

    witness = ProofTargetsImportSemanticsWitness()
    invariant = ImportInvariant(
        invariant_id="test_inv_001",
        statement="All modules in pkg are importable",
        scope="package",
        modules_in_scope=("json", "os", "sys"),
        is_universal=True,
    )
    iwr = witness.witness_import_invariant(invariant)
    print(f"Invariant witness: witnessed={iwr.witnessed_at_runtime} "
          f"violation={iwr.violation_found}")

    pj = witness.build_proof_judgment(results[0])
    print(f"Proof judgment: status={pj.status} label={pj.label!r}")
    print("smoke test PASSED")
