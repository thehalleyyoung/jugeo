"""Integration of scope and state analysis with the JuGeo framework.

This module connects the scope-and-state analysis results produced by the
algorithms in :mod:`jugeo.python_runtime.scope_and_state.algorithms` to the
broader JuGeo framework.  It is the *boundary layer* between the raw scope
analysis and the judgment algebra, coordinate system, and Z3 solver.

Responsibilities of this module:

* **Judgment emission** (:class:`ScopeJudgmentEmitter`): converts scope
  analysis artefacts (name bindings, scope sections, closure records, module
  manifests) into :class:`~jugeo.judgments.judgment_terms.Judgment` objects
  that can be stored in the JuGeo judgment repository.

* **Z3 constraint encoding** (:class:`Z3ScopeEncoder`): translates scope
  well-formedness properties into SMT-LIB2 formula strings that can be
  checked by a :class:`~jugeo.solver.z3_session.Z3Session`.

* **Coordinate mapping** (:class:`ScopeCoordinateMapper`): constructs
  :class:`~jugeo.geometry.site.CoordinateObject` instances from Python AST
  node descriptions, maintaining a bidirectional index.

* **Support region construction** (:class:`SupportRegionBuilder`): builds
  :class:`~jugeo.geometry.supports.SupportRegion` objects from scope data so
  that support-jurisdiction checks can be performed across the framework.

* **Copilot integration advice** (:class:`CopilotScopeAdvisor`): provides
  human-readable annotations, refactoring suggestions, and scope reports that
  are surfaced through the GitHub Copilot integration layer.

Theory reference: theory2.tex Ch15 — *Scope, State, and Sheaf-Theoretic
Name Resolution in the JuGeo Python Runtime Analyser*.

Note: the copilot keyword appears throughout this module's public API
because this module is the primary surface through which GitHub Copilot
consumes scope analysis results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from jugeo.geometry.site import (
    CoordinateObject,
    CoordinateKind,
    MorphismKind,
    Site,
    SiteBuilder,
)
from jugeo.geometry.supports import (
    SupportRegion,
    SupportSet,
    SupportedSection,
    SupportTracker,
)
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentStatus,
    Obstruction,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    ResidualObligation,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.solver.z3_session import (
    SolveOutcome,
    Z3Formula,
    Z3Session,
    z3_available,
)

from jugeo.python_runtime.scope_and_state.models import (
    BindingMap,
    ClosureRecord,
    ModuleStateManifest,
    NameCoordinate,
    NameKind,
    NameResolutionResult,
    ScopeChain,
    ScopeKind,
    ScopeSection,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: Mapping from :class:`ScopeKind` to the most appropriate
#: :class:`CoordinateKind` for building a :class:`CoordinateObject`.
_SCOPE_KIND_TO_COORD_KIND: dict[ScopeKind, CoordinateKind] = {
    ScopeKind.MODULE: CoordinateKind.MODULE,
    ScopeKind.FUNCTION: CoordinateKind.FUNCTION,
    ScopeKind.LAMBDA: CoordinateKind.FUNCTION,
    ScopeKind.CLASS: CoordinateKind.FUNCTION,
    ScopeKind.COMPREHENSION: CoordinateKind.REGION,
    ScopeKind.GENERATOR: CoordinateKind.REGION,
}


def _coord_from_scope(scope: ScopeSection) -> CoordinateObject:
    """Build a :class:`CoordinateObject` from a :class:`ScopeSection`.

    Splits ``scope.scope_key`` on ``"/"`` to obtain the component tuple and
    maps the scope's kind to the closest :class:`CoordinateKind`.

    Parameters:
        scope: The :class:`ScopeSection` to derive a coordinate from.

    Returns:
        A freshly constructed :class:`CoordinateObject`.
    """
    components = tuple(scope.scope_key.split("/"))
    kind = _SCOPE_KIND_TO_COORD_KIND.get(scope.scope_kind, CoordinateKind.FUNCTION)
    return CoordinateObject(components=components, kind=kind)


# ---------------------------------------------------------------------------
# ScopeJudgmentEmitter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScopeJudgmentEmitter:
    """Creates :class:`Judgment` objects from scope analysis results.

    Acts as the primary translation layer between the scope analysis
    subsystem and the JuGeo judgment algebra.  Every ``emit_*`` method
    appends the resulting :class:`Judgment` to the internal ``_emitted``
    list so that callers can batch-retrieve all judgments after an analysis
    pass.

    All emitted judgments have
    :attr:`~jugeo.judgments.judgment_terms.PropositionKind.STRUCTURAL` kind
    and carry a single
    :attr:`~jugeo.judgments.judgment_terms.EvidenceItemKind.ORACLE_PROPOSAL`
    evidence item as the primary evidence source.

    Attributes:
        module_coordinate: The :class:`CoordinateObject` representing the
            enclosing module, used as a fallback coordinate when scope-level
            coordinates cannot be determined.
        _emitted: Ordered list of all judgments emitted during this session.
        _emission_log: Human-readable log of emit operations for debugging.
    """

    module_coordinate: CoordinateObject
    _emitted: list[Judgment] = field(default_factory=list)
    _emission_log: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Emit methods
    # ------------------------------------------------------------------

    def emit_name_judgment(self, coord: NameCoordinate) -> Judgment:
        """Emit a :class:`Judgment` for a single name binding.

        Creates a structural proposition asserting that *coord.name* is bound
        in scope *coord.scope_key* with the given kind and type.  The
        nesting depth is approximated by the number of ``"/"`` separators in
        the scope key.

        Parameters:
            coord: The :class:`NameCoordinate` describing the binding.

        Returns:
            A :class:`Judgment` with ``PropositionKind.STRUCTURAL`` encoding
            the name-binding fact.
        """
        depth = coord.scope_key.count("/")
        formula = (
            f"name_bound('{coord.name}', scope='{coord.scope_key}', "
            f"kind='{coord.kind.value}', depth={depth}, "
            f"type='{coord.type_repr}')"
        )
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=(coord.name,),
        )
        carrier = Carrier(name="NameBindingCarrier")
        bundle = EvidenceBundle()
        item = EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={
                "name": coord.name,
                "scope_key": coord.scope_key,
                "kind": coord.kind.value,
                "type_repr": coord.type_repr,
                "depth": depth,
            },
        )
        bundle.add_evidence(item)
        # Derive a fine-grained coordinate: append the name as a leaf component.
        components = tuple(coord.scope_key.split("/")) + (coord.name,)
        j_coord = CoordinateObject(
            components=components, kind=CoordinateKind.REGION
        )
        j = Judgment(
            coordinate=j_coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
        )
        self._emitted.append(j)
        self._emission_log.append(
            f"emit_name_judgment: '{coord.name}' @ '{coord.scope_key}'"
        )
        return j

    def emit_scope_judgment(self, scope: ScopeSection) -> Judgment:
        """Emit a :class:`Judgment` for scope well-formedness.

        The proposition asserts that *scope* is syntactically and
        semantically well-formed: it has a valid kind, a consistent parent
        reference, and a non-negative binding count.

        Parameters:
            scope: The :class:`ScopeSection` to characterise.

        Returns:
            A :class:`Judgment` encoding the scope's structure.
        """
        depth = scope.scope_key.count("/")
        binding_count = len(scope.bindings)
        formula = (
            f"well_formed_scope("
            f"key='{scope.scope_key}', "
            f"kind='{scope.scope_kind.value}', "
            f"bindings={binding_count}, "
            f"depth={depth}, "
            f"parent={repr(scope.parent_key)})"
        )
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=(scope.scope_key,),
        )
        carrier = Carrier(name="ScopeWellFormedCarrier")
        bundle = EvidenceBundle()
        item = EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={
                "scope_key": scope.scope_key,
                "scope_kind": scope.scope_kind.value,
                "binding_count": binding_count,
                "parent_key": scope.parent_key,
                "depth": depth,
                "source_location": scope.source_location,
            },
        )
        bundle.add_evidence(item)
        coord = _coord_from_scope(scope)
        j = Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
        )
        self._emitted.append(j)
        self._emission_log.append(
            f"emit_scope_judgment: '{scope.scope_key}' "
            f"({scope.scope_kind.value}, {binding_count} bindings)"
        )
        return j

    def emit_closure_judgment(self, record: ClosureRecord) -> Judgment:
        """Emit a :class:`Judgment` for closure correctness.

        The proposition asserts that the closure identified by
        ``record.function_key`` correctly captures its free variables from
        the declared enclosing scopes, with no unaccounted-for free
        references.

        Parameters:
            record: The :class:`ClosureRecord` to validate.

        Returns:
            A :class:`Judgment` encoding closure well-formedness.
        """
        free_names = list(record.all_free_names)
        formula = (
            f"closure_correct("
            f"func='{record.function_key}', "
            f"free_vars={free_names}, "
            f"enclosing={list(record.enclosing_keys)}, "
            f"depth={record.depth})"
        )
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=tuple(free_names),
        )
        carrier = Carrier(name="ClosureCorrectnessCarrier")
        bundle = EvidenceBundle()
        item = EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={
                "function_key": record.function_key,
                "free_variables": free_names,
                "enclosing_keys": list(record.enclosing_keys),
                "depth": record.depth,
                "free_var_count": len(record.free_variables),
            },
        )
        bundle.add_evidence(item)
        components = tuple(record.function_key.split("/"))
        coord = CoordinateObject(
            components=components, kind=CoordinateKind.FUNCTION
        )
        j = Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
        )
        self._emitted.append(j)
        self._emission_log.append(
            f"emit_closure_judgment: '{record.function_key}' "
            f"({len(free_names)} free vars)"
        )
        return j

    def emit_module_state_judgment(
        self, manifest: ModuleStateManifest
    ) -> Judgment:
        """Emit a :class:`Judgment` for module state consistency.

        The proposition asserts that the module namespace recorded in
        *manifest* is internally consistent: the ``global_names`` tuple is
        non-empty and every name has an associated type representation.

        Parameters:
            manifest: The :class:`ModuleStateManifest` snapshot to validate.

        Returns:
            A :class:`Judgment` encoding the module state consistency claim.
        """
        name_count = len(manifest.global_names)
        formula = (
            f"module_state_consistent("
            f"module='{manifest.module_name}', "
            f"name_count={name_count}, "
            f"version={manifest.version})"
        )
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=tuple(manifest.global_names),
        )
        carrier = Carrier(name="ModuleStateCarrier")
        bundle = EvidenceBundle()
        item = EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={
                "module_name": manifest.module_name,
                "global_names": list(manifest.global_names),
                "version": manifest.version,
                "name_count": name_count,
            },
        )
        bundle.add_evidence(item)
        j = Judgment(
            coordinate=manifest.module_coordinate,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
        )
        self._emitted.append(j)
        self._emission_log.append(
            f"emit_module_state_judgment: '{manifest.module_name}' "
            f"v{manifest.version} ({name_count} names)"
        )
        return j

    def batch_emit(self, scopes: list[ScopeSection]) -> list[Judgment]:
        """Emit one scope judgment per entry in *scopes*.

        Parameters:
            scopes: List of :class:`ScopeSection` objects to process.

        Returns:
            Ordered list of emitted :class:`Judgment` objects, one per scope.
        """
        return [self.emit_scope_judgment(scope) for scope in scopes]

    # ------------------------------------------------------------------
    # Evidence and provenance helpers
    # ------------------------------------------------------------------

    def build_evidence(
        self, source: str, payload: dict[str, Any]
    ) -> EvidenceItem:
        """Create an :class:`EvidenceItem` with oracle-proposal kind.

        Parameters:
            source: Human-readable description of the evidence source,
                e.g. ``"scope_analysis"``.
            payload: Arbitrary key/value evidence payload.

        Returns:
            An :class:`EvidenceItem` with
            ``kind=EvidenceItemKind.ORACLE_PROPOSAL``.
        """
        return EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={"source": source, **payload},
        )

    def build_provenance(
        self, parent_judgments: list[str]
    ) -> Provenance:
        """Build a :class:`Provenance` object for derived judgments.

        Parameters:
            parent_judgments: List of parent judgment identifier strings
                (typically content hashes or coordinate keys).

        Returns:
            A :class:`Provenance` with ``source=ProvenanceSource.ORACLE``
            and the given parent judgment chain.
        """
        return Provenance(
            source=ProvenanceSource.ORACLE,
            parent_judgments=tuple(parent_judgments),
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def all_emitted(self) -> list[Judgment]:
        """Return a copy of the accumulated emitted judgments list.

        Returns:
            Shallow copy of ``_emitted``.
        """
        return list(self._emitted)

    def clear(self) -> None:
        """Reset the emitted judgments list and the emission log."""
        self._emitted.clear()
        self._emission_log.clear()
        logger.debug(
            "ScopeJudgmentEmitter cleared for module coordinate '%s'",
            self.module_coordinate.key,
        )


# ---------------------------------------------------------------------------
# Z3ScopeEncoder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Z3ScopeEncoder:
    """Encodes scope well-formedness properties as SMT-LIB2 formula strings.

    Generates SMT-LIB2 formula strings that capture structural invariants of
    Python scopes.  These strings can be parsed back into Z3 assertions via
    :meth:`~jugeo.solver.z3_session.Z3Serializer.smt2_to_formula` when Z3
    is available.

    The integration with :class:`~jugeo.solver.z3_session.Z3Session` is
    intentionally lightweight: this class generates the formula text and
    accumulates it in ``_formulas``; the caller decides whether to feed the
    text into a live Z3 session.

    Attributes:
        _formulas: Ordered list of all formula strings generated so far.
        _formula_count: Count of formulas generated (monotonically
            increasing); may diverge from ``len(_formulas)`` if
            :meth:`reset` is called.
    """

    _formulas: list[str] = field(default_factory=list)
    _formula_count: int = 0

    def encode_name_uniqueness(self, scope: ScopeSection) -> str:
        """Return an SMT-LIB2 assertion that all binding names in *scope* are distinct.

        Declares each binding name as an SMT ``String`` constant and emits a
        single ``(assert (distinct …))`` clause.  If the scope has fewer than
        two bindings the assertion degenerates to ``(assert true)``.

        Parameters:
            scope: The :class:`ScopeSection` whose binding names must be
                distinct.

        Returns:
            Multi-line SMT-LIB2 formula string.
        """
        names = [b.name for b in scope.bindings]
        if len(names) < 2:
            formula = "; name uniqueness trivially satisfied (< 2 bindings)\n(assert true)"
            self._formulas.append(formula)
            self._formula_count += 1
            return formula

        # Sanitise names to valid SMT-LIB2 identifiers.
        safe_names = [
            f"binding_{i}_{name.replace('.', '_').replace('-', '_')}"
            for i, name in enumerate(names)
        ]
        decl_lines = [
            f"(declare-const {sn} String)" for sn in safe_names
        ]
        # Assert each declared constant equals its original name string.
        eq_lines = [
            f"(assert (= {sn} \"{orig}\"))"
            for sn, orig in zip(safe_names, names)
        ]
        distinct_args = " ".join(safe_names)
        assert_line = f"(assert (distinct {distinct_args}))"
        formula = "\n".join(decl_lines + eq_lines + [assert_line])
        self._formulas.append(formula)
        self._formula_count += 1
        return formula

    def encode_scope_covering(
        self,
        scopes: list[ScopeSection],
        module_names: list[str],
    ) -> str:
        """Assert that every module-level name is bound in at least one scope.

        For each name in *module_names*, declares a boolean ``covered_<name>``
        variable and asserts it is true (if the name is present in some
        scope's bindings) or false (if not).

        Parameters:
            scopes: All scopes in the module.
            module_names: The global name strings that must be covered.

        Returns:
            Multi-line SMT-LIB2 formula string.
        """
        if not module_names:
            formula = "(assert true) ; no module names to cover"
            self._formulas.append(formula)
            self._formula_count += 1
            return formula

        # Build the union of all bound names across all scopes.
        all_bound: set[str] = {
            b.name for scope in scopes for b in scope.bindings
        }

        decl_lines: list[str] = []
        assert_lines: list[str] = []
        for name in module_names:
            safe = f"covered_{name.replace('.', '_').replace('-', '_')}"
            decl_lines.append(f"(declare-const {safe} Bool)")
            if name in all_bound:
                assert_lines.append(
                    f"(assert {safe}) ; '{name}' is covered"
                )
            else:
                assert_lines.append(
                    f"(assert (not {safe})) ; '{name}' is NOT covered"
                )

        formula = "\n".join(decl_lines + assert_lines)
        self._formulas.append(formula)
        self._formula_count += 1
        return formula

    def encode_closure_well_formedness(
        self, record: ClosureRecord
    ) -> str:
        """Assert that all free variables in *record* are bound in some enclosing scope.

        Declares a boolean ``has_enclosing_<name>`` for each free-variable
        name and asserts it is true, encoding the requirement that the
        enclosing scope chain supplies every captured variable.

        Parameters:
            record: The :class:`ClosureRecord` whose free variables are
                checked.

        Returns:
            Multi-line SMT-LIB2 formula string.
        """
        if not record.all_free_names:
            formula = (
                f"; closure '{record.function_key}' has no free variables\n"
                f"(assert true)"
            )
            self._formulas.append(formula)
            self._formula_count += 1
            return formula

        lines: list[str] = [
            f"; closure well-formedness for '{record.function_key}'"
        ]
        for name in record.all_free_names:
            safe = (
                f"has_enclosing_{name.replace('.', '_').replace('-', '_')}"
            )
            lines.append(f"(declare-const {safe} Bool)")
            lines.append(
                f"(assert {safe}) ; '{name}' must be bound in enclosing scope"
            )

        formula = "\n".join(lines)
        self._formulas.append(formula)
        self._formula_count += 1
        return formula

    def encode_module_state_consistency(
        self, manifest: ModuleStateManifest
    ) -> str:
        """Assert that the module global names set is non-empty.

        Encodes a simple count assertion: the module must have at least one
        globally-bound name.

        Parameters:
            manifest: The :class:`ModuleStateManifest` to check.

        Returns:
            Multi-line SMT-LIB2 formula string.
        """
        count = len(manifest.global_names)
        formula = (
            f"; module state consistency for '{manifest.module_name}'\n"
            f"(declare-const module_name_count Int)\n"
            f"(assert (= module_name_count {count}))\n"
            f"(assert (>= module_name_count 1)) ; at least one global name"
        )
        self._formulas.append(formula)
        self._formula_count += 1
        return formula

    def check_scope_constraints(
        self,
        scope: ScopeSection,
        session: Z3Session | None = None,
    ) -> SolveOutcome:
        """Encode scope constraints and optionally check satisfiability.

        If ``z3_available()`` returns ``True`` and *session* is provided,
        calls ``session.check_sat()`` after encoding the name-uniqueness
        constraint.  Otherwise returns
        :attr:`~jugeo.solver.z3_session.SolveOutcome.UNKNOWN`.

        Parameters:
            scope: The :class:`ScopeSection` to check.
            session: An optional live :class:`Z3Session`.  If ``None`` the
                formula is generated but not checked.

        Returns:
            The :class:`SolveOutcome` from Z3, or ``UNKNOWN`` if Z3 is
            unavailable or no session was provided.
        """
        formula_str = self.encode_name_uniqueness(scope)
        logger.debug(
            "Encoded name-uniqueness formula for scope '%s' (%d chars)",
            scope.scope_key,
            len(formula_str),
        )

        if not z3_available():
            logger.debug("Z3 not available; returning UNKNOWN")
            return SolveOutcome.UNKNOWN

        if session is None:
            logger.debug("No Z3Session provided; returning UNKNOWN")
            return SolveOutcome.UNKNOWN

        try:
            result = session.check_sat()
            # Z3Session.check_sat() may return Z3Result or SolverResult.
            if hasattr(result, "outcome"):
                return result.outcome  # type: ignore[return-value]
            if hasattr(result, "status"):
                return result.status  # type: ignore[return-value]
            return SolveOutcome.UNKNOWN
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Z3 constraint check failed for scope '%s': %s",
                scope.scope_key,
                exc,
            )
            return SolveOutcome.UNKNOWN

    def all_formulas(self) -> list[str]:
        """Return a copy of all accumulated formula strings.

        Returns:
            Shallow copy of ``_formulas``.
        """
        return list(self._formulas)

    def reset(self) -> None:
        """Clear all accumulated formulas.  Does not reset ``_formula_count``."""
        self._formulas.clear()
        logger.debug(
            "Z3ScopeEncoder reset; %d formulas cleared", self._formula_count
        )


# ---------------------------------------------------------------------------
# ScopeCoordinateMapper
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScopeCoordinateMapper:
    """Maps Python AST node descriptions to :class:`CoordinateObject` instances.

    Maintains a bidirectional index so that scope keys (slash-separated
    strings) and coordinate keys (slash-separated component paths as returned
    by :attr:`CoordinateObject.key`) can be cross-referenced efficiently.

    Attributes:
        module_name: Dotted module name used as the root component for all
            coordinate construction.
        _coordinate_index: Maps description strings (scope keys, function
            names, etc.) to the corresponding
            :class:`CoordinateObject`.
        _reverse_index: Maps ``coord.key`` strings back to the originating
            description string.
    """

    module_name: str
    _coordinate_index: dict[str, CoordinateObject] = field(
        default_factory=dict
    )
    _reverse_index: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Coordinate constructors
    # ------------------------------------------------------------------

    def map_module(self, module_name: str) -> CoordinateObject:
        """Create and register a module-level coordinate.

        Parameters:
            module_name: Dotted module name, e.g. ``"mypackage.utils"``.
                Will be stored as a single-component tuple after replacing
                ``"."`` with ``"/"``.

        Returns:
            :class:`CoordinateObject` with ``kind=CoordinateKind.MODULE``.
        """
        # Use the last component as the canonical module leaf name.
        components = (module_name,)
        coord = CoordinateObject(
            components=components, kind=CoordinateKind.MODULE
        )
        self._coordinate_index[module_name] = coord
        self._reverse_index[coord.key] = module_name
        return coord

    def map_function(
        self, func_name: str, parent_components: tuple[str, ...]
    ) -> CoordinateObject:
        """Create and register a function coordinate.

        Parameters:
            func_name: Bare function name, e.g. ``"process_items"``.
            parent_components: Component tuple of the enclosing scope.

        Returns:
            :class:`CoordinateObject` with ``kind=CoordinateKind.FUNCTION``.
        """
        components = parent_components + (func_name,)
        coord = CoordinateObject(
            components=components, kind=CoordinateKind.FUNCTION
        )
        description = "/".join(components)
        self._coordinate_index[description] = coord
        self._reverse_index[coord.key] = description
        return coord

    def map_class(
        self, class_name: str, parent_components: tuple[str, ...]
    ) -> CoordinateObject:
        """Create and register a class coordinate.

        Uses ``CoordinateKind.FUNCTION`` because no dedicated CLASS kind
        exists in the site category (classes are function-like coordinators
        in the sheaf model).

        Parameters:
            class_name: Bare class name, e.g. ``"MyProcessor"``.
            parent_components: Component tuple of the enclosing scope.

        Returns:
            :class:`CoordinateObject` with ``kind=CoordinateKind.FUNCTION``.
        """
        components = parent_components + (class_name,)
        coord = CoordinateObject(
            components=components, kind=CoordinateKind.FUNCTION
        )
        description = "/".join(components)
        self._coordinate_index[description] = coord
        self._reverse_index[coord.key] = description
        return coord

    def map_comprehension(
        self,
        var_name: str,
        parent_components: tuple[str, ...],
        index: int = 0,
    ) -> CoordinateObject:
        """Create and register a comprehension scope coordinate.

        Comprehension scopes are named ``"comp_<index>_<var_name>"`` to avoid
        collisions when multiple comprehensions share the same iteration
        variable name.

        Parameters:
            var_name: The iteration variable name, e.g. ``"x"``.
            parent_components: Component tuple of the enclosing scope.
            index: Disambiguating integer index within the parent scope.

        Returns:
            :class:`CoordinateObject` with ``kind=CoordinateKind.REGION``.
        """
        comp_name = f"comp_{index}_{var_name}"
        components = parent_components + (comp_name,)
        coord = CoordinateObject(
            components=components, kind=CoordinateKind.REGION
        )
        description = "/".join(components)
        self._coordinate_index[description] = coord
        self._reverse_index[coord.key] = description
        return coord

    def map_lambda(
        self,
        parent_components: tuple[str, ...],
        index: int = 0,
    ) -> CoordinateObject:
        """Create and register a lambda scope coordinate.

        Lambda scopes are named ``"lambda_<index>"`` to allow multiple
        lambdas in the same parent scope.

        Parameters:
            parent_components: Component tuple of the enclosing scope.
            index: Disambiguating integer index within the parent scope.

        Returns:
            :class:`CoordinateObject` with ``kind=CoordinateKind.FUNCTION``.
        """
        lambda_name = f"lambda_{index}"
        components = parent_components + (lambda_name,)
        coord = CoordinateObject(
            components=components, kind=CoordinateKind.FUNCTION
        )
        description = "/".join(components)
        self._coordinate_index[description] = coord
        self._reverse_index[coord.key] = description
        return coord

    def build_coordinate_index(
        self, scopes: list[ScopeSection]
    ) -> dict[str, CoordinateObject]:
        """Index all scopes in *scopes* by their scope key.

        Iterates over every scope, constructs the appropriate coordinate, and
        registers it in both ``_coordinate_index`` and ``_reverse_index``.

        Parameters:
            scopes: List of :class:`ScopeSection` objects to index.

        Returns:
            Dictionary mapping scope key strings to
            :class:`CoordinateObject` instances.
        """
        for scope in scopes:
            coord = _coord_from_scope(scope)
            self._coordinate_index[scope.scope_key] = coord
            self._reverse_index[coord.key] = scope.scope_key
        return dict(self._coordinate_index)

    def lookup(self, key: str) -> CoordinateObject | None:
        """Look up a :class:`CoordinateObject` by its description or scope key.

        Parameters:
            key: The description string (scope key or constructed description)
                to look up.

        Returns:
            The :class:`CoordinateObject` if found, else ``None``.
        """
        return self._coordinate_index.get(key)

    def all_coordinates(self) -> list[CoordinateObject]:
        """Return all registered coordinates as a list.

        Returns:
            List of all :class:`CoordinateObject` values in insertion order.
        """
        return list(self._coordinate_index.values())


# ---------------------------------------------------------------------------
# SupportRegionBuilder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SupportRegionBuilder:
    """Builds :class:`SupportRegion` objects for scopes and modules.

    Translates the scope analysis data structures into the support-region
    vocabulary used by the JuGeo support-jurisdiction system
    (theory2.tex Ch15 §5.1).

    In this encoding, the ``patch_keys`` of a :class:`SupportRegion` are the
    string names of the bindings in the corresponding scope, and ``labels``
    carry the scope kind.

    Attributes:
        module_coordinate: The module-level :class:`CoordinateObject` used as
            the root coordinate for module-level support regions.
        _regions: Ordered list of all support regions built during this
            session.
    """

    module_coordinate: CoordinateObject
    _regions: list[SupportRegion] = field(default_factory=list)

    def build_scope_support(self, scope: ScopeSection) -> SupportRegion:
        """Build a :class:`SupportRegion` for a single scope.

        The region's ``patch_keys`` are the frozenset of all binding name
        strings in *scope*, and its ``labels`` carry the scope kind.

        Parameters:
            scope: The :class:`ScopeSection` to build a region for.

        Returns:
            A :class:`SupportRegion` whose coordinate is derived from the
            scope key.
        """
        coord = _coord_from_scope(scope)
        patch_keys: frozenset[str] = frozenset(
            b.name for b in scope.bindings
        )
        labels: frozenset[str] = frozenset({scope.scope_kind.value})
        region = SupportRegion(
            coordinate=coord,
            patch_keys=patch_keys,
            labels=labels,
        )
        self._regions.append(region)
        return region

    def build_closure_support(self, record: ClosureRecord) -> SupportRegion:
        """Build a :class:`SupportRegion` for a closure.

        The region's ``patch_keys`` are the free-variable name strings of the
        closure and its ``labels`` include ``"closure"`` and the nesting depth.

        Parameters:
            record: The :class:`ClosureRecord` to build a region for.

        Returns:
            A :class:`SupportRegion` whose coordinate is derived from the
            closure's function key.
        """
        components = tuple(record.function_key.split("/"))
        coord = CoordinateObject(
            components=components, kind=CoordinateKind.FUNCTION
        )
        patch_keys: frozenset[str] = frozenset(record.all_free_names)
        labels: frozenset[str] = frozenset(
            {"closure", f"depth_{record.depth}"}
        )
        region = SupportRegion(
            coordinate=coord,
            patch_keys=patch_keys,
            labels=labels,
        )
        self._regions.append(region)
        return region

    def build_module_support(
        self, manifest: ModuleStateManifest
    ) -> SupportRegion:
        """Build a :class:`SupportRegion` for the module global namespace.

        The region's ``patch_keys`` are the globally-bound name strings from
        *manifest* and its ``labels`` include ``"module"`` and the version.

        Parameters:
            manifest: The :class:`ModuleStateManifest` snapshot to encode.

        Returns:
            A :class:`SupportRegion` at the module coordinate.
        """
        patch_keys: frozenset[str] = frozenset(manifest.global_names)
        labels: frozenset[str] = frozenset(
            {"module", f"v{manifest.version}"}
        )
        region = SupportRegion(
            coordinate=manifest.module_coordinate,
            patch_keys=patch_keys,
            labels=labels,
        )
        self._regions.append(region)
        return region

    def merge_supports(self, regions: list[SupportRegion]) -> SupportSet:
        """Merge multiple :class:`SupportRegion` patch_keys into one :class:`SupportSet`.

        Collects the union of all ``patch_keys`` (binding name strings) from
        every region in *regions* and constructs a :class:`SupportSet`.

        Parameters:
            regions: List of :class:`SupportRegion` objects to merge.

        Returns:
            A :class:`SupportSet` whose ``coordinates`` are the union of all
            ``patch_keys`` across the input regions.
        """
        all_keys: frozenset[str] = frozenset().union(
            *(r.patch_keys for r in regions)
        ) if regions else frozenset()
        return SupportSet(coordinates=all_keys)

    def validate_support_coverage(
        self,
        scope_supports: list[SupportRegion],
        module_support: SupportRegion,
    ) -> bool:
        """Check that the union of scope patch_keys covers all module patch_keys.

        A module name is *covered* if at least one scope in *scope_supports*
        has it in its ``patch_keys``.  Returns ``True`` only if every name in
        the module's patch_keys is covered.

        Parameters:
            scope_supports: List of scope-level :class:`SupportRegion` objects.
            module_support: The module-level :class:`SupportRegion` whose
                ``patch_keys`` define the coverage requirement.

        Returns:
            ``True`` if coverage is complete, ``False`` otherwise.
        """
        all_scope_keys: set[str] = set()
        for region in scope_supports:
            all_scope_keys.update(region.patch_keys)
        uncovered = module_support.patch_keys - all_scope_keys
        if uncovered:
            logger.debug(
                "Module support coverage gap: %d uncovered names: %s",
                len(uncovered),
                sorted(uncovered)[:5],
            )
        return len(uncovered) == 0

    def all_regions(self) -> list[SupportRegion]:
        """Return a copy of all built :class:`SupportRegion` objects.

        Returns:
            Shallow copy of ``_regions``.
        """
        return list(self._regions)


# ---------------------------------------------------------------------------
# CopilotScopeAdvisor
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CopilotScopeAdvisor:
    """Copilot integration layer for scope analysis advice.

    Provides human-readable annotations, rename suggestions, shadowing
    detection, refactoring recommendations, and formatted scope reports.
    Every public method prefixes output strings with ``# copilot:`` so
    they can be distinguished from regular code comments.

    This class is the primary surface through which GitHub Copilot consumes
    the results of scope analysis (theory2.tex Ch15 §6).

    Attributes:
        module_name: Dotted name of the module being advised.
        _advice_log: Ordered list of all advice strings generated during
            this session.
    """

    module_name: str
    _advice_log: list[str] = field(default_factory=list)

    def suggest_rename(
        self, coord: NameCoordinate, reason: str
    ) -> str:
        """Return a copilot rename suggestion comment.

        Parameters:
            coord: The :class:`NameCoordinate` of the name to rename.
            reason: A short human-readable justification for the rename.

        Returns:
            A ``# copilot:`` comment string.
        """
        suggestion = (
            f"# copilot: consider renaming '{coord.name}' "
            f"(scope='{coord.scope_key}', kind={coord.kind.value}) "
            f"because {reason}"
        )
        self._advice_log.append(suggestion)
        return suggestion

    def explain_resolution(
        self, result: NameResolutionResult
    ) -> str:
        """Return a human-readable explanation of how a name was resolved.

        Parameters:
            result: The :class:`NameResolutionResult` to explain.

        Returns:
            A multi-sentence explanation string.
        """
        if not result.resolved:
            path_str = " → ".join(result.resolution_path) or "(no scopes searched)"
            explanation = (
                f"# copilot: name '{result.name}' could NOT be resolved.\n"
                f"# Searched scopes (innermost first): {path_str}\n"
                f"# Error: {result.error_message or 'name not found in any scope'}"
            )
        else:
            assert result.coordinate is not None
            depth = result.coordinate.scope_key.count("/")
            path_str = " → ".join(result.resolution_path)
            explanation = (
                f"# copilot: name '{result.name}' resolved as "
                f"{result.coordinate.kind.value} in scope '{result.scope_key}' "
                f"(nesting depth {depth}).\n"
                f"# LEGB path: {path_str}\n"
                f"# Type: {result.coordinate.type_repr}"
            )
        self._advice_log.append(explanation)
        return explanation

    def detect_shadowing(
        self,
        inner_scope: ScopeSection,
        outer_scope: ScopeSection,
    ) -> list[str]:
        """Find names in *inner_scope* that shadow names in *outer_scope*.

        A name *shadows* if it appears in both ``inner_scope.bindings`` and
        ``outer_scope.bindings``.  These situations may indicate bugs or at
        least surprising behaviour.

        Parameters:
            inner_scope: The nested scope to check for shadows.
            outer_scope: The enclosing scope to compare against.

        Returns:
            Sorted list of shadowed name strings.
        """
        inner_names = {b.name for b in inner_scope.bindings}
        outer_names = {b.name for b in outer_scope.bindings}
        shadowed = sorted(inner_names & outer_names)
        if shadowed:
            msg = (
                f"# copilot: '{inner_scope.scope_key}' shadows "
                f"{len(shadowed)} name(s) from '{outer_scope.scope_key}': "
                f"{shadowed}"
            )
            self._advice_log.append(msg)
        return shadowed

    def suggest_scope_refactoring(
        self, scope: ScopeSection
    ) -> list[str]:
        """Generate refactoring suggestions for a scope.

        Heuristic rules applied:

        * Deep nesting (depth > 4): suggest extraction.
        * Global variable declarations: suggest removing the ``global``
          statement.
        * High binding count (> 20): suggest splitting the scope.
        * Comprehension or generator containing closures: flag potential
          late-binding issues.

        Parameters:
            scope: The :class:`ScopeSection` to analyse.

        Returns:
            List of ``# copilot:`` suggestion strings (may be empty).
        """
        suggestions: list[str] = []
        depth = scope.scope_key.count("/")

        if depth > 4:
            suggestions.append(
                f"# copilot: consider extracting the deeply nested scope "
                f"'{scope.scope_key}' (depth={depth}) into a top-level function"
            )

        global_vars = [
            b.name for b in scope.bindings if b.kind == NameKind.GLOBAL
        ]
        for gv in global_vars:
            suggestions.append(
                f"# copilot: avoid global variable '{gv}' in "
                f"'{scope.scope_key}'; pass it as a parameter instead"
            )

        nonlocal_vars = [
            b.name for b in scope.bindings if b.kind == NameKind.NONLOCAL
        ]
        for nlv in nonlocal_vars:
            suggestions.append(
                f"# copilot: nonlocal '{nlv}' in '{scope.scope_key}' "
                f"creates tight coupling; consider a class or container instead"
            )

        binding_count = len(scope.bindings)
        if binding_count > 20:
            suggestions.append(
                f"# copilot: scope '{scope.scope_key}' has {binding_count} "
                f"bindings; consider splitting into smaller functions for "
                f"readability"
            )

        if scope.scope_kind in (ScopeKind.COMPREHENSION, ScopeKind.GENERATOR):
            free_vars = [
                b.name
                for b in scope.bindings
                if b.kind in (NameKind.FREE, NameKind.CLOSURE)
            ]
            if free_vars:
                suggestions.append(
                    f"# copilot: comprehension '{scope.scope_key}' closes over "
                    f"{free_vars}; beware of late-binding in loops"
                )

        self._advice_log.extend(suggestions)
        return suggestions

    def format_scope_report(
        self, scopes: list[ScopeSection]
    ) -> str:
        """Return a multi-line formatted report of all scopes.

        Produces a human-readable, indented listing of scopes and their
        bindings, suitable for display in a Copilot chat panel or CI log.

        Parameters:
            scopes: List of :class:`ScopeSection` objects to report on.

        Returns:
            Multi-line string report.
        """
        lines: list[str] = [
            "# copilot: scope report — theory2.tex Ch15 scope analysis",
            f"# module: {self.module_name}",
            f"# total scopes: {len(scopes)}",
            "",
        ]
        # Sort by scope key for deterministic output.
        for scope in sorted(scopes, key=lambda s: s.scope_key):
            depth = scope.scope_key.count("/")
            indent = "  " * depth
            binding_count = len(scope.bindings)
            lines.append(
                f"{indent}[{scope.scope_kind.value}] {scope.scope_key} "
                f"({binding_count} binding{'s' if binding_count != 1 else ''})"
            )
            for binding in scope.bindings:
                kind_tag = binding.kind.value
                type_tag = (
                    f": {binding.type_repr}"
                    if binding.type_repr != "unknown"
                    else ""
                )
                lines.append(
                    f"{indent}  ├─ {binding.name} [{kind_tag}]{type_tag}"
                )
            if scope.parent_key:
                lines.append(f"{indent}  └─ parent: {scope.parent_key}")
            lines.append("")

        report = "\n".join(lines)
        self._advice_log.append(
            f"format_scope_report: generated {len(lines)}-line report"
        )
        return report

    def generate_copilot_annotation(
        self, scope: ScopeSection
    ) -> str:
        """Return a docstring-style copilot annotation for a scope.

        Produces a formatted annotation block that can be inserted above a
        function or class definition to communicate scope analysis results
        to GitHub Copilot in subsequent completions.

        Parameters:
            scope: The :class:`ScopeSection` to annotate.

        Returns:
            A triple-quoted docstring string suitable for insertion into
            source code.
        """
        depth = scope.scope_key.count("/")
        binding_names = [b.name for b in scope.bindings]
        preview = (
            ", ".join(binding_names[:8])
            + (" …" if len(binding_names) > 8 else "")
        )
        free_names = [
            b.name
            for b in scope.bindings
            if b.kind in (NameKind.FREE, NameKind.NONLOCAL, NameKind.CLOSURE)
        ]
        annotation_lines: list[str] = [
            '"""',
            f"copilot scope annotation — theory2.tex Ch15",
            f"",
            f"Scope key   : {scope.scope_key}",
            f"Kind        : {scope.scope_kind.value}",
            f"Depth       : {depth}",
            f"Parent      : {scope.parent_key or '(root)'}",
            f"Bindings    : {len(binding_names)} — {preview}",
            f"Free vars   : {free_names or '[]'}",
            f"Source      : {scope.source_location or '(unknown)'}",
            f'"""',
        ]
        annotation = "\n".join(annotation_lines)
        self._advice_log.append(
            f"generate_copilot_annotation: '{scope.scope_key}'"
        )
        return annotation


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ScopeJudgmentEmitter",
    "Z3ScopeEncoder",
    "ScopeCoordinateMapper",
    "SupportRegionBuilder",
    "CopilotScopeAdvisor",
]
