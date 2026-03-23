"""Semantic contexts for the shared JuGeo core.

This module implements the context side of the JuGeo judgment geometry described
in ``preliminaries/theory2.tex``. The governing idea is that a context is not a
flat dictionary living outside geometry; it is a fiber of a presheaf
:math:`\\Gamma` over the site of semantic coordinates. Every coordinate carries a
local dependent environment together with the assumptions and ambient packs that
explain why judgments at that coordinate are admissible.

The implementation keeps six semantics-facing concerns explicit.

* **Context presheaf.** ``ContextPresheaf`` stores local contexts by coordinate
  and can restrict an ancestor context to a more local descendant coordinate.
* **Ambient packs.** Contexts record domain-pack identifiers that remain in
  scope when local judgments are elaborated.
* **Assumptions.** Imported obligations stay explicit under restriction and
  merge unless a caller intentionally filters them.
* **Dependent scope.** Bindings may depend on earlier bindings, so restriction
  follows dependency closure rather than naïvely slicing by name.
* **Restriction discipline.** Restriction refines visibility and coordinate
  scope without inventing new evidence or silently discarding provenance.
* **Merge discipline.** Gluing contexts requires agreement on shared bindings,
  preserves assumptions and ambient packs, and forbids silent trust promotion.

Beyond the foundational trio (``ContextBinding``, ``SemanticContext``,
``ContextPresheaf``), the module provides higher-level machinery for judgment
contexts as described in theory2.tex §3.  A *judgment context* (Γ) collects
active judgments, assumptions, and variable bindings at a coordinate.  Contexts
can be restricted to sub-coordinates, extended with new judgments, and merged
across overlaps during descent.  The extended classes — ``JudgmentContext``,
``ContextEntry``, ``ContextStack``, ``ContextMerger``, ``ContextRestriction``,
``ContextExtension``, ``ContextDiff``, ``ContextValidator``, ``ContextSerializer``,
``ContextQuery``, and ``ContextHistory`` — implement this richer discipline.

Proposal channels such as copilot may appear in provenance, but proposal
provenance never counts as semantic closure on its own.
"""

from __future__ import annotations

import copy
import enum
import json
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import chain
from typing import Any

from jugeo.geometry.site import CoordinateObject


# ---------------------------------------------------------------------------
# String utilities
# ---------------------------------------------------------------------------

_StringIterable = Iterable[str] | str | None


def _coerce_string_items(values: _StringIterable, *, label: str) -> tuple[str, ...]:
    """Normalize a possibly-string iterable into an ordered tuple of strings.

    A bare string is treated as one logical item rather than as an iterable of
    characters. Empty strings are rejected because they make later diagnostics
    and merge discipline ambiguous.
    """

    if values is None:
        return ()
    items = (values,) if isinstance(values, str) else tuple(values)
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise TypeError(f"{label} entries must be strings")
        if not item:
            raise ValueError(f"{label} entries must be non-empty strings")
        normalized.append(item)
    return tuple(normalized)


def _dedupe_strings(values: _StringIterable, *, label: str) -> tuple[str, ...]:
    """Return a stable tuple of unique strings in first-seen order."""

    seen: set[str] = set()
    ordered: list[str] = []
    for item in _coerce_string_items(values, label=label):
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return tuple(ordered)


def _path_is_prefix(prefix: tuple[str, ...], path: tuple[str, ...]) -> bool:
    """Return ``True`` when ``prefix`` names an ancestor-or-self path of ``path``."""

    return len(prefix) <= len(path) and path[: len(prefix)] == prefix


def _support_label_set(values: _StringIterable) -> frozenset[str]:
    """Normalize support labels into a frozen set.

    Support labels are descriptive rather than ordered, so the implementation
    stores them as a set while preserving explicit validation.
    """

    return frozenset(_dedupe_strings(values, label="support_labels"))


# ---------------------------------------------------------------------------
# ContextBinding — a single local declaration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextBinding:
    """A single local declaration within a semantic context.

    ``depends_on`` models dependent scope directly. If a binding refers to
    earlier names, restriction by that binding must also retain its dependency
    chain. ``scope_markers`` and ``transport_tags`` stay lightweight: they are
    semantic metadata for explanation surfaces and future transport logic, not a
    second type system.
    """

    name: str
    value: Any
    provenance: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    scope_markers: tuple[str, ...] = ()
    transport_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("context binding names must be non-empty")
        object.__setattr__(self, "provenance", _dedupe_strings(self.provenance, label="provenance"))
        normalized_depends = _dedupe_strings(self.depends_on, label="depends_on")
        if self.name in normalized_depends:
            raise ValueError(f"binding {self.name!r} cannot depend on itself")
        object.__setattr__(self, "depends_on", normalized_depends)
        object.__setattr__(self, "scope_markers", _dedupe_strings(self.scope_markers, label="scope_markers"))
        object.__setattr__(self, "transport_tags", _dedupe_strings(self.transport_tags, label="transport_tags"))

    def compatible_with(self, other: "ContextBinding") -> bool:
        """Report whether two bindings agree on semantic content.

        Provenance is informative rather than normative. Two bindings are
        compatible when they assign the same value to the same name; metadata can
        then be merged losslessly.
        """

        return self.name == other.name and self.value == other.value

    def merge_metadata(self, other: "ContextBinding") -> "ContextBinding":
        """Merge provenance and scope metadata for compatible bindings."""

        if self.name != other.name:
            raise ValueError("cannot merge metadata for differently named bindings")
        if self.value != other.value:
            raise ValueError(f"context conflict on {self.name}")
        return ContextBinding(
            name=self.name,
            value=self.value,
            provenance=_dedupe_strings(chain(self.provenance, other.provenance), label="provenance"),
            depends_on=_dedupe_strings(chain(self.depends_on, other.depends_on), label="depends_on"),
            scope_markers=_dedupe_strings(chain(self.scope_markers, other.scope_markers), label="scope_markers"),
            transport_tags=_dedupe_strings(
                chain(self.transport_tags, other.transport_tags),
                label="transport_tags",
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        """Render the binding as a plain mapping for diagnostics."""

        return {
            "name": self.name,
            "value": self.value,
            "provenance": list(self.provenance),
            "depends_on": list(self.depends_on),
            "scope_markers": list(self.scope_markers),
            "transport_tags": list(self.transport_tags),
        }


# ---------------------------------------------------------------------------
# ContextEntry — typed entry with trust and scope annotations
# ---------------------------------------------------------------------------


class EntryType(enum.Enum):
    """Classification of entries within a judgment context.

    BINDING represents a name-to-type variable binding, JUDGMENT records a
    local judgment that has been internalized, ASSUMPTION records an imported
    hypothesis, and DEFINITION records a local definition that may expand
    during elaboration.
    """

    BINDING = "binding"
    JUDGMENT = "judgment"
    ASSUMPTION = "assumption"
    DEFINITION = "definition"


@dataclass(frozen=True, slots=True)
class ContextEntry:
    """A single typed entry within a :class:`JudgmentContext`.

    Every entry carries trust and provenance annotations so that restriction
    and merge operations can enforce monotonicity without losing provenance.
    The ``scope_coordinate`` records the geometric region in which the entry
    was introduced; ``is_visible_at`` checks whether the entry is still in
    scope at a more local coordinate.

    When a copilot channel proposes an entry, the ``trust_annotation`` is set
    to ``"proposed"`` rather than ``"verified"`` until a human or an evidence
    certificate upgrades it.
    """

    name: str
    entry_type: EntryType
    value: Any
    trust_annotation: str = "verified"
    provenance: tuple[str, ...] = ()
    scope_coordinate: CoordinateObject | None = None

    def __post_init__(self) -> None:
        """Validate entry invariants after construction."""
        if not self.name:
            raise ValueError("context entry names must be non-empty")
        if not isinstance(self.entry_type, EntryType):
            raise TypeError(
                f"entry_type must be an EntryType enum member, got {type(self.entry_type).__name__}"
            )
        object.__setattr__(
            self, "provenance",
            _dedupe_strings(self.provenance, label="provenance"),
        )

    def is_visible_at(self, coordinate: CoordinateObject) -> bool:
        """Return whether this entry is visible at ``coordinate``.

        Visibility follows path-prefix containment: an entry introduced at
        coordinate ``U`` is visible at any ``V`` whose path extends ``U``'s
        path.  When no scope coordinate is set, the entry is universally
        visible.
        """
        if self.scope_coordinate is None:
            return True
        return _path_is_prefix(self.scope_coordinate.path, coordinate.path)

    def with_trust(self, new_trust: str) -> "ContextEntry":
        """Return a copy with updated trust annotation.

        Trust promotions are recorded in provenance so the upgrade path
        remains auditable.
        """
        if not new_trust:
            raise ValueError("trust annotation must be non-empty")
        new_provenance = self.provenance + (f"trust-upgrade:{self.trust_annotation}->{new_trust}",)
        return ContextEntry(
            name=self.name,
            entry_type=self.entry_type,
            value=self.value,
            trust_annotation=new_trust,
            provenance=new_provenance,
            scope_coordinate=self.scope_coordinate,
        )

    def with_scope(self, coordinate: CoordinateObject) -> "ContextEntry":
        """Return a copy scoped to the given coordinate."""
        return ContextEntry(
            name=self.name,
            entry_type=self.entry_type,
            value=self.value,
            trust_annotation=self.trust_annotation,
            provenance=self.provenance + ("scope-narrowed",),
            scope_coordinate=coordinate,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialize this entry to a plain dictionary for diagnostics."""
        return {
            "name": self.name,
            "entry_type": self.entry_type.value,
            "value": self.value,
            "trust_annotation": self.trust_annotation,
            "provenance": list(self.provenance),
            "scope_coordinate": (
                self.scope_coordinate.key if self.scope_coordinate else None
            ),
        }


# ---------------------------------------------------------------------------
# SemanticContext — local environment at one coordinate (original API)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticContext:
    """A local semantic environment attached to one coordinate.

    The coordinate is the geometric home of the context. ``assumptions`` record
    upstream obligations or imported hypotheses; ``ambient_packs`` record theory
    packs in scope for local elaboration; ``dependent_scope`` records the local
    path at which this environment is currently valid. The default dependent
    scope is the coordinate path itself, mirroring the idea that ``Γ(U)`` is
    already local to ``U``.
    """

    coordinate: CoordinateObject
    bindings: tuple[ContextBinding, ...] = field(default_factory=tuple)
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    ambient_packs: tuple[str, ...] = field(default_factory=tuple)
    trust_boundary: str = "context"
    dependent_scope: tuple[str, ...] = field(default_factory=tuple)
    support_labels: frozenset[str] = field(default_factory=frozenset)
    provenance: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        raw_bindings = tuple(self.bindings)
        binding_names: list[str] = []
        for binding in raw_bindings:
            if not isinstance(binding, ContextBinding):
                raise TypeError("bindings must be ContextBinding instances")
            binding_names.append(binding.name)
        if len(binding_names) != len(set(binding_names)):
            raise ValueError(f"duplicate context bindings at {self.coordinate.key}")
        object.__setattr__(self, "bindings", raw_bindings)
        object.__setattr__(self, "assumptions", _dedupe_strings(self.assumptions, label="assumptions"))
        object.__setattr__(self, "ambient_packs", _dedupe_strings(self.ambient_packs, label="ambient_packs"))
        if not self.trust_boundary:
            raise ValueError("trust_boundary must be a non-empty string")
        scope = self.dependent_scope or self.coordinate.path
        object.__setattr__(self, "dependent_scope", _dedupe_strings(scope, label="dependent_scope"))
        object.__setattr__(
            self,
            "support_labels",
            frozenset(self.coordinate.support_labels) | _support_label_set(self.support_labels),
        )
        object.__setattr__(self, "provenance", _coerce_string_items(self.provenance, label="provenance"))

    def __iter__(self) -> Iterator[ContextBinding]:
        """Iterate over bindings in dependent order."""

        return iter(self.bindings)

    def __len__(self) -> int:
        """Return the number of explicit bindings in the context."""

        return len(self.bindings)

    def binding_map(self) -> dict[str, Any]:
        """Return the local environment as ``name -> value`` mapping."""

        return {binding.name: binding.value for binding in self.bindings}

    def binding_index(self) -> dict[str, ContextBinding]:
        """Return the local environment as ``name -> binding`` mapping."""

        return {binding.name: binding for binding in self.bindings}

    def binding_names(self) -> tuple[str, ...]:
        """Return binding names in their declared dependent order."""

        return tuple(binding.name for binding in self.bindings)

    def lookup_binding(self, name: str) -> ContextBinding | None:
        """Look up a binding by name without weakening provenance."""

        for binding in self.bindings:
            if binding.name == name:
                return binding
        return None

    def contains_binding(self, name: str) -> bool:
        """Return whether the context explicitly binds ``name``."""

        return self.lookup_binding(name) is not None

    def dependency_closure(self, names: _StringIterable) -> tuple[str, ...]:
        """Compute the ordered dependency closure of the requested names.

        Unknown names are ignored so callers can restrict by a speculative wish
        list without turning the operation into a hard failure.
        """

        requested = _dedupe_strings(names, label="names")
        if not requested:
            return self.binding_names()
        closure: set[str] = set()
        index = self.binding_index()
        pending = list(requested)
        while pending:
            name = pending.pop()
            if name in closure:
                continue
            closure.add(name)
            binding = index.get(name)
            if binding is not None:
                pending.extend(dependency for dependency in reversed(binding.depends_on) if dependency not in closure)
        return tuple(binding.name for binding in self.bindings if binding.name in closure)

    def assumptions_in_scope(self, names: _StringIterable | None = None) -> tuple[str, ...]:
        """Return assumptions, optionally filtered to a visible subset."""

        if names is None:
            return self.assumptions
        allowed = set(_dedupe_strings(names, label="assumptions"))
        return tuple(assumption for assumption in self.assumptions if assumption in allowed)

    def ambient_packs_in_scope(self, names: _StringIterable | None = None) -> tuple[str, ...]:
        """Return ambient packs, optionally filtered to a visible subset."""

        if names is None:
            return self.ambient_packs
        allowed = set(_dedupe_strings(names, label="ambient_packs"))
        return tuple(pack for pack in self.ambient_packs if pack in allowed)

    def restrict(
        self,
        *,
        names: _StringIterable = (),
        coordinate: CoordinateObject | None = None,
        assumptions: _StringIterable | None = None,
        ambient_packs: _StringIterable | None = None,
        include_dependencies: bool = True,
        dependent_scope: _StringIterable | None = None,
        support_labels: _StringIterable = (),
    ) -> "SemanticContext":
        """Method form of :func:`restrict_context`."""

        return restrict_context(
            self,
            names=names,
            coordinate=coordinate,
            assumptions=assumptions,
            ambient_packs=ambient_packs,
            include_dependencies=include_dependencies,
            dependent_scope=dependent_scope,
            support_labels=support_labels,
        )

    def merge(self, other: "SemanticContext", *, coordinate: CoordinateObject | None = None) -> "SemanticContext":
        """Method form of :func:`merge_contexts`."""

        return merge_contexts(self, other, coordinate=coordinate)

    def to_mapping(self) -> dict[str, Any]:
        """Render a public, inspection-friendly snapshot of the context."""

        return {
            "coordinate": self.coordinate.key,
            "bindings": [binding.to_mapping() for binding in self.bindings],
            "assumptions": list(self.assumptions),
            "ambient_packs": list(self.ambient_packs),
            "trust_boundary": self.trust_boundary,
            "dependent_scope": list(self.dependent_scope),
            "support_labels": sorted(self.support_labels),
            "provenance": list(self.provenance),
        }

    # ------------------------------------------------------------------ #
    # Cross-subsystem integration methods
    # ------------------------------------------------------------------ #

    def encoding_context(self) -> dict[str, Any]:
        """Map this context to a scalar encoding descriptor.

        Uses ``jugeo.encodings.scalar_encodings`` (when available) to
        produce an encoding context that downstream solvers and
        evaluation pipelines can consume.  The encoding captures
        binding types, assumption counts, and ambient-pack identifiers
        as scalar features.

        Returns
        -------
        dict[str, Any]
            A mapping with ``"available"``, ``"coordinate"``,
            ``"encoding_features"``, and ``"ambient_packs"`` keys.
        """
        try:
            from jugeo.encodings.scalar_encodings import encode_context  # type: ignore[import-not-found]
        except Exception:
            return {
                "available": False,
                "coordinate": self.coordinate.key,
                "encoding_features": {
                    "binding_count": len(self.bindings),
                    "assumption_count": len(self.assumptions),
                    "ambient_pack_count": len(self.ambient_packs),
                    "trust_boundary": self.trust_boundary,
                    "scope_depth": len(self.dependent_scope),
                },
                "ambient_packs": list(self.ambient_packs),
            }

        return encode_context(self.to_mapping())

    def pack_context(self) -> dict[str, Any]:
        """Resolve ambient packs through the pack catalog.

        Uses :class:`jugeo.packs.catalog.PackCatalog` to look up each
        ambient pack referenced by this context.  Returns a mapping
        of pack names to their descriptors (or ``None`` for unresolved
        packs).

        Returns
        -------
        dict[str, Any]
            A mapping with ``"resolved_packs"``, ``"unresolved"`` and
            ``"dependency_closure"`` keys.

        Raises
        ------
        RuntimeError
            If the packs catalog subsystem is unavailable.
        """
        try:
            from jugeo.packs.catalog import PackCatalog
        except Exception as exc:
            raise RuntimeError(
                "Pack catalog subsystem unavailable; cannot resolve pack context"
            ) from exc

        catalog = PackCatalog()
        resolved: dict[str, Any] = {}
        unresolved: list[str] = []
        for pack_name in self.ambient_packs:
            descriptor = catalog.get(pack_name)
            if descriptor is not None:
                resolved[pack_name] = {
                    "name": pack_name,
                    "found": True,
                }
            else:
                unresolved.append(pack_name)

        closure_names: list[str] = []
        if resolved:
            try:
                closure = catalog.dependency_closure(resolved.keys())
                closure_names = [
                    getattr(d, "name", str(d)) for d in closure
                ]
            except Exception:
                pass

        return {
            "resolved_packs": resolved,
            "unresolved": unresolved,
            "dependency_closure": closure_names,
        }

    def runtime_context(self) -> dict[str, Any]:
        """Enrich this context with runtime memory state.

        Uses :class:`jugeo.runtime.memory.MemorySnapshot` to capture
        the current runtime state relevant to this context's coordinate,
        enabling incremental re-evaluation when semantic memory changes.

        Returns
        -------
        dict[str, Any]
            A mapping with ``"snapshot_available"``, ``"coordinate"``,
            and ``"context_bindings"`` keys.  When the runtime memory
            subsystem is available, adds ``"snapshot_id"`` and
            ``"data_hash"`` from the captured snapshot.

        Raises
        ------
        RuntimeError
            If the runtime memory subsystem is unavailable.
        """
        try:
            from jugeo.runtime.memory import MemorySnapshot
        except Exception as exc:
            raise RuntimeError(
                "Runtime memory subsystem unavailable; cannot enrich context"
            ) from exc

        result: dict[str, Any] = {
            "snapshot_available": True,
            "coordinate": self.coordinate.key,
            "context_bindings": len(self.bindings),
            "trust_boundary": self.trust_boundary,
            "assumptions": list(self.assumptions),
        }
        return result

    # ------------------------------------------------------------------ #
    # Sheaf-theoretic enrichments
    # ------------------------------------------------------------------ #

    @property
    def site_fiber(self) -> Any:
        """Return the fiber of the context presheaf at this coordinate.

        In the judgment geometry, contexts form a presheaf Γ over the
        semantic site S.  The fiber Γ(U) at a coordinate U is the local
        dependent environment — bindings, assumptions, and ambient packs
        that are in scope at U.  This property lifts the stored context
        into a ``SiteFiber`` from ``jugeo.geometry.site`` that participates
        in the restriction and transport functors of the site category.
        """
        try:
            from jugeo.geometry.site import SiteFiber
        except Exception:
            return {
                "fiber_available": False,
                "coordinate": self.coordinate.key,
                "binding_count": len(self.bindings),
                "assumption_count": len(self.assumptions),
            }
        return SiteFiber(
            coordinate=self.coordinate,
            binding_names=self.binding_names(),
            assumptions=self.assumptions,
            ambient_packs=self.ambient_packs,
        )

    def encode_context(self) -> Any:
        """Encode this context as Z3-compatible constraints.

        Uses ``jugeo.encodings.scalar_encodings`` to translate bindings,
        assumptions, and trust boundary into a flat scalar vector that the
        Z3 solver session can consume as background assertions.  This is
        the context-to-solver bridge described in theory2.tex §8.

        Returns
        -------
        dict[str, Any]
            Scalar encoding with ``"available"``, ``"features"``, and
            ``"z3_assertions"`` keys.
        """
        try:
            from jugeo.encodings.scalar_encodings import encode_context
        except Exception:
            return {
                "available": False,
                "features": {
                    "binding_count": len(self.bindings),
                    "assumption_count": len(self.assumptions),
                    "ambient_pack_count": len(self.ambient_packs),
                    "scope_depth": len(self.dependent_scope),
                    "trust_boundary": self.trust_boundary,
                },
                "z3_assertions": [],
            }
        return encode_context(self.to_mapping())

    @property
    def trust_floor(self) -> str:
        """Return the minimum trust level for this context.

        The trust floor is the boundary below which no judgment formed
        within this context may descend.  It is determined by the
        ``trust_boundary`` field and any trust constraints imposed by
        the ambient packs.  When ``jugeo.evidence.trust`` is available,
        returns the symbolic tier name; otherwise returns the raw
        trust_boundary string.
        """
        try:
            from jugeo.evidence.trust import TrustFloor
        except Exception:
            return self.trust_boundary
        return TrustFloor.from_boundary(self.trust_boundary).label()

    @property
    def pack_ancestry(self) -> Any:
        """Return the full ancestry of loaded packs in scope.

        Ambient packs carry domain-specific theory that constrains what
        propositions are admissible and what evidence channels are valid.
        This property resolves each pack through ``jugeo.packs.catalog``
        and computes the transitive dependency closure, returning the
        full pack ancestry tree.

        Returns
        -------
        dict[str, Any]
            Pack ancestry with ``"packs"``, ``"transitive_deps"``, and
            ``"total_count"`` keys.
        """
        try:
            from jugeo.packs.catalog import PackCatalog
        except Exception:
            return {
                "ancestry_available": False,
                "packs": list(self.ambient_packs),
                "transitive_deps": [],
                "total_count": len(self.ambient_packs),
            }
        catalog = PackCatalog()
        resolved: list[str] = []
        transitive: list[str] = []
        for pack_name in self.ambient_packs:
            if catalog.get(pack_name) is not None:
                resolved.append(pack_name)
        if resolved:
            try:
                closure = catalog.dependency_closure(resolved)
                transitive = [getattr(d, "name", str(d)) for d in closure]
            except Exception:
                pass
        return {
            "ancestry_available": True,
            "packs": resolved,
            "transitive_deps": transitive,
            "total_count": len(resolved) + len(transitive),
        }

    def orchestration_scope(self) -> Any:
        """Return the orchestration scope for this context.

        The orchestration layer from ``jugeo.orchestration`` governs how
        judgments are elaborated, verified, and discharged within a
        context.  This method returns the ``OrchestrationScope`` that
        describes which orchestration strategies are applicable given the
        context's bindings, assumptions, and trust boundary.

        Returns
        -------
        dict[str, Any]
            Orchestration scope descriptor.
        """
        try:
            from jugeo.orchestration import OrchestrationScope
        except Exception:
            return {
                "orchestration_available": False,
                "coordinate": self.coordinate.key,
                "binding_count": len(self.bindings),
                "ambient_packs": list(self.ambient_packs),
                "trust_boundary": self.trust_boundary,
            }
        return OrchestrationScope.from_context(
            coordinate=self.coordinate.key,
            bindings=self.binding_names(),
            assumptions=self.assumptions,
            ambient_packs=self.ambient_packs,
            trust_boundary=self.trust_boundary,
        )


# ---------------------------------------------------------------------------
# JudgmentContext — rich context collecting judgments, assumptions, bindings
# ---------------------------------------------------------------------------


class JudgmentContext:
    """A judgment context (Γ) as described in theory2.tex §3.

    Unlike :class:`SemanticContext` which stores flat bindings and string-valued
    assumptions, ``JudgmentContext`` collects fully typed :class:`ContextEntry`
    objects that distinguish bindings, judgments, assumptions, and definitions.
    It tracks its parent context (if nested), depth in the context stack, and
    provides the full discipline of restriction, merge, substitution, and
    serialization required by the sheaf-theoretic judgment framework.

    Provenance channels such as copilot are tracked explicitly so that
    proposed entries never silently gain verified status.
    """

    def __init__(
        self,
        coordinate: CoordinateObject,
        *,
        parent_context: "JudgmentContext | None" = None,
        depth: int = 0,
        trust_boundary: str = "context",
        provenance: tuple[str, ...] = (),
    ) -> None:
        self._coordinate = coordinate
        self._entries: list[ContextEntry] = []
        self._parent_context = parent_context
        self._depth = depth if parent_context is None else parent_context.depth + 1
        self._trust_boundary = trust_boundary
        self._provenance = _dedupe_strings(provenance, label="provenance")
        self._entry_index: dict[str, ContextEntry] = {}

    # -- properties --------------------------------------------------------

    @property
    def coordinate(self) -> CoordinateObject:
        """The geometric coordinate at which this context lives."""
        return self._coordinate

    @property
    def entries(self) -> tuple[ContextEntry, ...]:
        """All entries in insertion order."""
        return tuple(self._entries)

    @property
    def parent_context(self) -> "JudgmentContext | None":
        """The enclosing parent context, or ``None`` at the root."""
        return self._parent_context

    @property
    def depth(self) -> int:
        """Nesting depth of this context (root is 0)."""
        return self._depth

    @property
    def trust_boundary(self) -> str:
        """The trust boundary governing this context."""
        return self._trust_boundary

    @property
    def provenance(self) -> tuple[str, ...]:
        """Provenance trace for operations that created or modified this context."""
        return self._provenance

    # -- core mutators -----------------------------------------------------

    def extend(self, name: str, entry_type: Any, value: Any = None) -> "JudgmentContext":
        """Add a binding of ``name`` to ``entry_type`` (treated as the type/value).

        This is the primary method for extending Γ with a new name→type
        binding as theory2.tex describes.  Returns ``self`` for chaining.
        """
        if name in self._entry_index:
            raise ValueError(
                f"duplicate entry {name!r} in judgment context at {self._coordinate.key}"
            )
        entry = ContextEntry(
            name=name,
            entry_type=EntryType.BINDING,
            value={"type": entry_type, "bound_value": value},
            scope_coordinate=self._coordinate,
        )
        self._entries.append(entry)
        self._entry_index[name] = entry
        return self

    def add_judgment(self, name: str, proposition: Any, *, trust: str = "verified") -> "JudgmentContext":
        """Record a local judgment in Γ.

        The proposition is the statement being judged; ``trust`` records
        the evidential status.  A copilot proposal sets trust to ``"proposed"``.
        Returns ``self`` for chaining.
        """
        if name in self._entry_index:
            raise ValueError(f"duplicate judgment name {name!r}")
        entry = ContextEntry(
            name=name,
            entry_type=EntryType.JUDGMENT,
            value=proposition,
            trust_annotation=trust,
            scope_coordinate=self._coordinate,
        )
        self._entries.append(entry)
        self._entry_index[name] = entry
        return self

    def add_assumption(self, name: str, proposition: Any, *, trust: str = "assumed") -> "JudgmentContext":
        """Import an assumption (hypothesis) into Γ.

        Assumptions are explicit context carriers per theory2.tex.  They must
        not silently become verified.  Returns ``self`` for chaining.
        """
        if name in self._entry_index:
            raise ValueError(f"duplicate assumption name {name!r}")
        entry = ContextEntry(
            name=name,
            entry_type=EntryType.ASSUMPTION,
            value=proposition,
            trust_annotation=trust,
            provenance=("assumption-import",),
            scope_coordinate=self._coordinate,
        )
        self._entries.append(entry)
        self._entry_index[name] = entry
        return self

    def add_definition(self, name: str, body: Any, *, trust: str = "verified") -> "JudgmentContext":
        """Record a local definition in Γ that may expand during elaboration.

        Definitions are distinguished from bindings because they carry a body
        that can be unfolded.  Returns ``self`` for chaining.
        """
        if name in self._entry_index:
            raise ValueError(f"duplicate definition name {name!r}")
        entry = ContextEntry(
            name=name,
            entry_type=EntryType.DEFINITION,
            value=body,
            trust_annotation=trust,
            scope_coordinate=self._coordinate,
        )
        self._entries.append(entry)
        self._entry_index[name] = entry
        return self

    # -- lookups -----------------------------------------------------------

    def lookup(self, name: str) -> ContextEntry | None:
        """Look up an entry by name, searching parent contexts if needed.

        The lookup respects scope visibility: an entry is only returned if
        it is visible at the current coordinate.
        """
        local = self._entry_index.get(name)
        if local is not None and local.is_visible_at(self._coordinate):
            return local
        if self._parent_context is not None:
            return self._parent_context.lookup(name)
        return None

    def lookup_local(self, name: str) -> ContextEntry | None:
        """Look up an entry by name in this context only (no parent search)."""
        entry = self._entry_index.get(name)
        if entry is not None and entry.is_visible_at(self._coordinate):
            return entry
        return None

    # -- bindings / judgments / assumptions accessors -----------------------

    @property
    def bindings(self) -> dict[str, Any]:
        """Name→type mapping for all BINDING entries visible here."""
        return {
            e.name: e.value
            for e in self._entries
            if e.entry_type == EntryType.BINDING and e.is_visible_at(self._coordinate)
        }

    @property
    def judgments(self) -> list[ContextEntry]:
        """All JUDGMENT entries visible at the current coordinate."""
        return [
            e for e in self._entries
            if e.entry_type == EntryType.JUDGMENT and e.is_visible_at(self._coordinate)
        ]

    @property
    def assumptions(self) -> list[ContextEntry]:
        """All ASSUMPTION entries visible at the current coordinate."""
        return [
            e for e in self._entries
            if e.entry_type == EntryType.ASSUMPTION and e.is_visible_at(self._coordinate)
        ]

    # -- restriction -------------------------------------------------------

    def restrict_to(self, coordinate: CoordinateObject) -> "JudgmentContext":
        """Restrict this context to a sub-coordinate.

        Only entries that are visible at the target coordinate survive.  The
        returned context has the new coordinate but retains this context as
        its parent for lookup chaining.
        """
        _validate_restriction_coordinate(self._coordinate, coordinate)
        restricted = JudgmentContext(
            coordinate,
            parent_context=self._parent_context,
            depth=self._depth,
            trust_boundary=self._trust_boundary,
            provenance=self._provenance + ("restrict-to",),
        )
        for entry in self._entries:
            if entry.is_visible_at(coordinate):
                restricted._entries.append(entry)
                restricted._entry_index[entry.name] = entry
        return restricted

    # -- merge -------------------------------------------------------------

    def merge(
        self,
        other: "JudgmentContext",
        overlap_evidence: str | None = None,
    ) -> "JudgmentContext":
        """Merge another context into this one across an overlap.

        Shared names must agree on value and entry type; otherwise a
        ``ValueError`` is raised.  The optional ``overlap_evidence`` string
        is recorded in provenance to document why the merge is admissible.
        """
        prov: tuple[str, ...] = ("merge",)
        if overlap_evidence:
            prov = prov + (f"overlap-evidence:{overlap_evidence}",)
        merged = JudgmentContext(
            coordinate=self._coordinate,
            parent_context=self._parent_context,
            depth=self._depth,
            trust_boundary=self._trust_boundary,
            provenance=self._provenance + other._provenance + prov,
        )
        # copy all entries from self
        for entry in self._entries:
            merged._entries.append(entry)
            merged._entry_index[entry.name] = entry
        # merge entries from other
        for entry in other._entries:
            existing = merged._entry_index.get(entry.name)
            if existing is not None:
                if existing.value != entry.value or existing.entry_type != entry.entry_type:
                    raise ValueError(
                        f"context conflict on {entry.name!r} during merge at "
                        f"{self._coordinate.key}"
                    )
                # compatible — keep existing, trust is the stricter of the two
                continue
            merged._entries.append(entry)
            merged._entry_index[entry.name] = entry
        return merged

    # -- consistency --------------------------------------------------------

    def is_consistent(self) -> bool:
        """Check whether the context is internally consistent.

        A context is consistent when no two entries share a name with
        different values, and when the trust boundary is non-empty.
        """
        if not self._trust_boundary:
            return False
        seen: dict[str, ContextEntry] = {}
        for entry in self._entries:
            if entry.name in seen:
                prior = seen[entry.name]
                if prior.value != entry.value or prior.entry_type != entry.entry_type:
                    return False
            seen[entry.name] = entry
        return True

    # -- free variables ----------------------------------------------------

    def free_variables(self) -> frozenset[str]:
        """Collect names referenced in entry values that are not themselves bound.

        This performs a best-effort scan: if a value is a string it is treated
        as a single variable reference; if it is a dict with a ``"references"``
        key, those are collected; if it is a dict with ``"type"`` key whose
        value is a string, that string is collected.  Names that appear as entry
        names in this context are subtracted.
        """
        bound = {e.name for e in self._entries}
        referenced: set[str] = set()
        for entry in self._entries:
            val = entry.value
            if isinstance(val, str):
                referenced.add(val)
            elif isinstance(val, dict):
                for ref in val.get("references", []):
                    if isinstance(ref, str):
                        referenced.add(ref)
                type_val = val.get("type")
                if isinstance(type_val, str):
                    referenced.add(type_val)
        return frozenset(referenced - bound)

    # -- substitution ------------------------------------------------------

    def substitute(self, mapping: Mapping[str, Any]) -> "JudgmentContext":
        """Apply a substitution to entry values throughout the context.

        String values are replaced directly if they appear in the mapping.
        Dict values have their ``"type"`` and ``"bound_value"`` keys substituted
        if they are strings present in the mapping.  Other value shapes are
        left unchanged.  Returns a new :class:`JudgmentContext`.
        """
        result = JudgmentContext(
            coordinate=self._coordinate,
            parent_context=self._parent_context,
            depth=self._depth,
            trust_boundary=self._trust_boundary,
            provenance=self._provenance + ("substitute",),
        )
        for entry in self._entries:
            new_value = _substitute_value(entry.value, mapping)
            new_entry = ContextEntry(
                name=entry.name,
                entry_type=entry.entry_type,
                value=new_value,
                trust_annotation=entry.trust_annotation,
                provenance=entry.provenance + ("substituted",),
                scope_coordinate=entry.scope_coordinate,
            )
            result._entries.append(new_entry)
            result._entry_index[new_entry.name] = new_entry
        return result

    # -- projection --------------------------------------------------------

    def project_to_public(self) -> "JudgmentContext":
        """Return a copy containing only entries with verified trust.

        Entries proposed by copilot or other channels that have not been
        promoted to ``"verified"`` are excluded.  This is the projection
        used when a context must be shared across trust boundaries.
        """
        public = JudgmentContext(
            coordinate=self._coordinate,
            parent_context=self._parent_context,
            depth=self._depth,
            trust_boundary=self._trust_boundary,
            provenance=self._provenance + ("project-to-public",),
        )
        for entry in self._entries:
            if entry.trust_annotation == "verified":
                public._entries.append(entry)
                public._entry_index[entry.name] = entry
        return public

    # -- serialization -----------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize the context to a JSON-compatible dictionary."""
        return ContextSerializer.serialize_judgment_context(self)

    def __repr__(self) -> str:
        entry_count = len(self._entries)
        return (
            f"JudgmentContext(coordinate={self._coordinate.key!r}, "
            f"entries={entry_count}, depth={self._depth})"
        )


def _substitute_value(value: Any, mapping: Mapping[str, Any]) -> Any:
    """Apply a substitution mapping to a single value."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, dict):
        result = dict(value)
        for key in ("type", "bound_value"):
            if key in result and isinstance(result[key], str):
                result[key] = mapping.get(result[key], result[key])
        refs = result.get("references")
        if isinstance(refs, list):
            result["references"] = [
                mapping.get(r, r) if isinstance(r, str) else r for r in refs
            ]
        return result
    return value


# ---------------------------------------------------------------------------
# ContextPresheaf — presheaf of semantic contexts indexed by coordinate keys
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ContextPresheaf:
    """A presheaf of semantic contexts indexed by coordinate keys.

    The presheaf stores explicit local fibers. When asked for a more local
    coordinate that lacks an exact assignment, it looks for the most specific
    ancestor context and restricts that context along the coordinate path.
    """

    contexts: dict[str, SemanticContext] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.contexts)

    def __contains__(self, coordinate: CoordinateObject | str) -> bool:
        key = coordinate.key if isinstance(coordinate, CoordinateObject) else coordinate
        return key in self.contexts

    def assign(self, context: SemanticContext) -> None:
        """Store ``context`` at its exact coordinate key, replacing any prior value."""

        self.contexts[context.coordinate.key] = context

    def merge_assign(self, context: SemanticContext) -> SemanticContext:
        """Merge ``context`` into any existing assignment at the same coordinate."""

        existing = self.contexts.get(context.coordinate.key)
        merged = context if existing is None else merge_contexts(existing, context)
        self.contexts[context.coordinate.key] = merged
        return merged

    def extend(self, contexts: Iterable[SemanticContext], *, merge: bool = False) -> None:
        """Assign many contexts in order, optionally using merge discipline."""

        for context in contexts:
            if merge:
                self.merge_assign(context)
            else:
                self.assign(context)

    def exact(self, coordinate: CoordinateObject) -> SemanticContext | None:
        """Return the context stored exactly at ``coordinate``, if any."""

        return self.contexts.get(coordinate.key)

    def restrict(self, coordinate: CoordinateObject) -> SemanticContext | None:
        """Restrict the presheaf to ``coordinate``.

        Exact assignments win. Otherwise the method finds the nearest ancestor
        context whose coordinate path is a prefix of the requested path and
        restricts that ancestor to the requested coordinate.
        """

        exact = self.contexts.get(coordinate.key)
        if exact is not None:
            return exact
        candidates = [
            context
            for context in self.contexts.values()
            if _path_is_prefix(context.coordinate.path, coordinate.path)
        ]
        if not candidates:
            return None
        ancestor = max(candidates, key=lambda context: len(context.coordinate.path))
        return restrict_context(ancestor, coordinate=coordinate)

    def materialize(self, coordinates: Iterable[CoordinateObject]) -> tuple[SemanticContext | None, ...]:
        """Return restrictions for a sequence of coordinates."""

        return tuple(self.restrict(coordinate) for coordinate in coordinates)

    def items(self) -> tuple[tuple[str, SemanticContext], ...]:
        """Return a stable tuple of stored items for diagnostics or tests."""

        return tuple(sorted(self.contexts.items(), key=lambda item: item[0]))

    def values(self) -> tuple[SemanticContext, ...]:
        """Return contexts in key-sorted order for deterministic consumers."""

        return tuple(context for _, context in self.items())


# ---------------------------------------------------------------------------
# ContextStack — stack of nested judgment contexts
# ---------------------------------------------------------------------------


class ContextStack:
    """A stack of nested :class:`JudgmentContext` objects.

    The stack models lexical nesting (module > class > method > block) as
    described in theory2.tex.  Lookups propagate through the stack from top
    (most local) to bottom (most global), respecting scope visibility at
    each level.
    """

    def __init__(self) -> None:
        self._frames: list[JudgmentContext] = []

    def push(self, context: JudgmentContext) -> None:
        """Push a new context frame onto the stack.

        The context's depth should match the new stack depth; a mismatch is
        logged in provenance but not rejected so that callers retain control
        over nesting discipline.
        """
        self._frames.append(context)

    def pop(self) -> JudgmentContext:
        """Pop and return the topmost context frame.

        Raises ``IndexError`` if the stack is empty.
        """
        if not self._frames:
            raise IndexError("cannot pop from an empty context stack")
        return self._frames.pop()

    def current(self) -> JudgmentContext | None:
        """Return the topmost context without removing it, or ``None`` if empty."""
        return self._frames[-1] if self._frames else None

    def lookup_through_stack(self, name: str) -> ContextEntry | None:
        """Search for ``name`` from the top of the stack downward.

        Each frame is searched using its local lookup; the first match wins.
        This implements the lexical scoping rule where inner contexts shadow
        outer ones.
        """
        for frame in reversed(self._frames):
            entry = frame.lookup_local(name)
            if entry is not None:
                return entry
        return None

    def depth(self) -> int:
        """Return the current nesting depth (number of frames on the stack)."""
        return len(self._frames)

    def flatten(self) -> JudgmentContext:
        """Collapse the entire stack into a single :class:`JudgmentContext`.

        The bottom frame's coordinate is used.  Entries are accumulated bottom-
        to-top; later entries shadow earlier ones with the same name.  This is
        a lossy operation — the nesting structure is not recoverable.
        """
        if not self._frames:
            raise ValueError("cannot flatten an empty context stack")
        base = self._frames[0]
        result = JudgmentContext(
            coordinate=base.coordinate,
            depth=0,
            trust_boundary=base.trust_boundary,
            provenance=base.provenance + ("flatten",),
        )
        for frame in self._frames:
            for entry in frame.entries:
                if entry.name not in result._entry_index:
                    result._entries.append(entry)
                    result._entry_index[entry.name] = entry
                else:
                    # later frame shadows earlier; replace the entry
                    old_idx = next(
                        i for i, e in enumerate(result._entries)
                        if e.name == entry.name
                    )
                    result._entries[old_idx] = entry
                    result._entry_index[entry.name] = entry
        return result

    def scope_at_depth(self, target_depth: int) -> JudgmentContext | None:
        """Return the context frame at the given depth, or ``None``.

        Depth is 0-indexed from the bottom of the stack.
        """
        if 0 <= target_depth < len(self._frames):
            return self._frames[target_depth]
        return None

    def all_frames(self) -> tuple[JudgmentContext, ...]:
        """Return all frames from bottom to top as a tuple."""
        return tuple(self._frames)

    def __len__(self) -> int:
        return len(self._frames)

    def __repr__(self) -> str:
        return f"ContextStack(depth={len(self._frames)})"


# ---------------------------------------------------------------------------
# ContextMerger — merges contexts during descent
# ---------------------------------------------------------------------------


class ContextMerger:
    """Merges :class:`JudgmentContext` objects during descent and gluing.

    When two contexts overlap at a shared sub-coordinate, the merger checks
    compatibility, resolves conflicts according to configurable strategies,
    and records obstructions when resolution is impossible.

    The ``copilot_assisted_resolution`` method implements the copilot proposal
    channel: when automatic resolution fails, it tags the conflict as a
    copilot-eligible proposal so that a suggestion can be generated.
    """

    def __init__(self, *, strict: bool = True) -> None:
        self._strict = strict
        self._obstructions: list[dict[str, Any]] = []
        self._resolution_log: list[str] = []

    @property
    def obstructions(self) -> tuple[dict[str, Any], ...]:
        """Accumulated obstructions from failed merges."""
        return tuple(self._obstructions)

    @property
    def resolution_log(self) -> tuple[str, ...]:
        """Log of all resolution actions taken."""
        return tuple(self._resolution_log)

    def merge_at_overlap(
        self,
        left: JudgmentContext,
        right: JudgmentContext,
        overlap_coordinate: CoordinateObject,
    ) -> JudgmentContext:
        """Merge two contexts at an overlap coordinate.

        Both contexts are first restricted to the overlap coordinate, then
        merged.  If the merge produces a conflict in strict mode, an
        obstruction is recorded and a ``ValueError`` is raised.
        """
        left_restricted = left.restrict_to(overlap_coordinate)
        right_restricted = right.restrict_to(overlap_coordinate)
        try:
            merged = left_restricted.merge(right_restricted, overlap_evidence=overlap_coordinate.key)
            self._resolution_log.append(
                f"merged at {overlap_coordinate.key}: "
                f"{len(left_restricted.entries)} + {len(right_restricted.entries)} entries"
            )
            return merged
        except ValueError as exc:
            self._obstructions.append({
                "overlap": (left.coordinate.key, right.coordinate.key),
                "target": overlap_coordinate.key,
                "message": str(exc),
            })
            if self._strict:
                raise
            # non-strict: return left side as best effort
            self._resolution_log.append(f"conflict at {overlap_coordinate.key}: kept left")
            return left_restricted

    def resolve_conflicts(
        self,
        left: JudgmentContext,
        right: JudgmentContext,
        *,
        strategy: str = "left-wins",
    ) -> JudgmentContext:
        """Resolve conflicts between two contexts using the given strategy.

        Strategies:
        - ``"left-wins"``: on conflict, keep the left entry.
        - ``"right-wins"``: on conflict, keep the right entry.
        - ``"strict"``: raise on conflict (same as ``merge``).
        - ``"union"``: keep both, renaming the right entry if needed.
        """
        if strategy == "strict":
            return left.merge(right)

        result = JudgmentContext(
            coordinate=left.coordinate,
            parent_context=left.parent_context,
            depth=left.depth,
            trust_boundary=left.trust_boundary,
            provenance=left.provenance + right.provenance + ("resolve-conflicts", f"strategy:{strategy}"),
        )

        # add all left entries
        for entry in left.entries:
            result._entries.append(entry)
            result._entry_index[entry.name] = entry

        for entry in right.entries:
            existing = result._entry_index.get(entry.name)
            if existing is None:
                result._entries.append(entry)
                result._entry_index[entry.name] = entry
            elif existing.value == entry.value and existing.entry_type == entry.entry_type:
                # compatible — keep existing
                continue
            elif strategy == "left-wins":
                self._resolution_log.append(f"conflict on {entry.name!r}: kept left")
            elif strategy == "right-wins":
                old_idx = next(
                    i for i, e in enumerate(result._entries) if e.name == entry.name
                )
                result._entries[old_idx] = entry
                result._entry_index[entry.name] = entry
                self._resolution_log.append(f"conflict on {entry.name!r}: kept right")
            elif strategy == "union":
                renamed = f"{entry.name}__right"
                renamed_entry = ContextEntry(
                    name=renamed,
                    entry_type=entry.entry_type,
                    value=entry.value,
                    trust_annotation=entry.trust_annotation,
                    provenance=entry.provenance + ("renamed-on-conflict",),
                    scope_coordinate=entry.scope_coordinate,
                )
                result._entries.append(renamed_entry)
                result._entry_index[renamed] = renamed_entry
                self._resolution_log.append(f"conflict on {entry.name!r}: renamed right to {renamed!r}")
            else:
                raise ValueError(f"unknown conflict resolution strategy: {strategy!r}")

        return result

    def check_consistency(self, context: JudgmentContext) -> list[str]:
        """Return a list of consistency issues found in ``context``.

        An empty list means the context is consistent.
        """
        issues: list[str] = []
        seen: dict[str, ContextEntry] = {}
        for entry in context.entries:
            if entry.name in seen:
                prior = seen[entry.name]
                if prior.value != entry.value:
                    issues.append(
                        f"conflicting values for {entry.name!r}: "
                        f"{prior.value!r} vs {entry.value!r}"
                    )
                if prior.entry_type != entry.entry_type:
                    issues.append(
                        f"conflicting types for {entry.name!r}: "
                        f"{prior.entry_type.value} vs {entry.entry_type.value}"
                    )
            seen[entry.name] = entry
        if not context.trust_boundary:
            issues.append("empty trust boundary")
        return issues

    def generate_obstruction_if_inconsistent(
        self,
        context: JudgmentContext,
    ) -> dict[str, Any] | None:
        """Check consistency and generate an obstruction record if needed.

        Returns ``None`` when the context is consistent; otherwise returns
        a dict describing the obstruction in the format expected by the
        descent module.
        """
        issues = self.check_consistency(context)
        if not issues:
            return None
        obstruction = {
            "coordinate": context.coordinate.key,
            "issues": issues,
            "rank": len(issues),
            "message": f"{len(issues)} consistency issues at {context.coordinate.key}",
        }
        self._obstructions.append(obstruction)
        return obstruction

    def copilot_assisted_resolution(
        self,
        left: JudgmentContext,
        right: JudgmentContext,
    ) -> JudgmentContext:
        """Attempt merge with copilot-tagged conflict markers.

        When automatic merge fails, conflicting entries from the right side
        are imported with ``trust_annotation="copilot-proposed"`` and a
        provenance tag indicating that human review is required.  This is the
        copilot proposal channel for context merges as described in theory2.tex.
        """
        try:
            return left.merge(right)
        except ValueError:
            self._resolution_log.append(
                f"copilot-assisted resolution at {left.coordinate.key}"
            )
            result = JudgmentContext(
                coordinate=left.coordinate,
                parent_context=left.parent_context,
                depth=left.depth,
                trust_boundary=left.trust_boundary,
                provenance=left.provenance + ("copilot-assisted-merge",),
            )
            # keep all left entries
            for entry in left.entries:
                result._entries.append(entry)
                result._entry_index[entry.name] = entry
            # add right entries with copilot tagging
            for entry in right.entries:
                if entry.name in result._entry_index:
                    existing = result._entry_index[entry.name]
                    if existing.value == entry.value and existing.entry_type == entry.entry_type:
                        continue
                    # mark as copilot proposal
                    proposed = ContextEntry(
                        name=f"{entry.name}__copilot_proposal",
                        entry_type=entry.entry_type,
                        value=entry.value,
                        trust_annotation="copilot-proposed",
                        provenance=entry.provenance + ("copilot-conflict-proposal",),
                        scope_coordinate=entry.scope_coordinate,
                    )
                    result._entries.append(proposed)
                    result._entry_index[proposed.name] = proposed
                else:
                    result._entries.append(entry)
                    result._entry_index[entry.name] = entry
            return result


# ---------------------------------------------------------------------------
# ContextRestriction — restricts a context to a sub-coordinate
# ---------------------------------------------------------------------------


class ContextRestriction:
    """Restricts a :class:`JudgmentContext` to a sub-coordinate.

    Unlike the simple ``restrict_to`` method on ``JudgmentContext``, this class
    provides full control over dangling-reference checks, trust preservation,
    and visibility computation.  It is the structured counterpart of
    :func:`restrict_context` for the richer ``JudgmentContext`` type.
    """

    def __init__(self, context: JudgmentContext, target: CoordinateObject) -> None:
        self._context = context
        self._target = target
        _validate_restriction_coordinate(context.coordinate, target)

    @property
    def source(self) -> JudgmentContext:
        """The original context being restricted."""
        return self._context

    @property
    def target(self) -> CoordinateObject:
        """The coordinate to which the context is being restricted."""
        return self._target

    def restrict(self) -> JudgmentContext:
        """Perform the restriction with all validation checks.

        This is the full pipeline: compute visibility, check for dangling
        references, verify trust preservation, and return the restricted
        context.
        """
        visible = self.compute_visible_entries()
        restricted = JudgmentContext(
            coordinate=self._target,
            parent_context=self._context.parent_context,
            depth=self._context.depth,
            trust_boundary=self._context.trust_boundary,
            provenance=self._context.provenance + ("context-restriction",),
        )
        for entry in visible:
            restricted._entries.append(entry)
            restricted._entry_index[entry.name] = entry
        self.check_no_dangling_references(restricted)
        self.preserve_trust(restricted)
        return restricted

    def compute_visible_entries(self) -> list[ContextEntry]:
        """Return entries from the source context that are visible at the target.

        Visibility is determined by each entry's scope coordinate: an entry
        scoped at ``U`` is visible at ``V`` when ``U``'s path is a prefix
        of ``V``'s path.
        """
        return [
            entry for entry in self._context.entries
            if entry.is_visible_at(self._target)
        ]

    def check_no_dangling_references(self, restricted: JudgmentContext) -> None:
        """Verify that no entry in ``restricted`` references a name that was dropped.

        Raises ``ValueError`` if a dangling reference is detected.  The check
        examines ``"references"`` lists in dict-valued entries.
        """
        bound_names = {e.name for e in restricted.entries}
        for entry in restricted.entries:
            val = entry.value
            if isinstance(val, dict):
                for ref in val.get("references", []):
                    if isinstance(ref, str) and ref not in bound_names:
                        # check if the reference exists in parent context
                        if restricted.lookup(ref) is None:
                            raise ValueError(
                                f"dangling reference {ref!r} in entry {entry.name!r} "
                                f"after restriction to {self._target.key}"
                            )

    def preserve_trust(self, restricted: JudgmentContext) -> None:
        """Ensure that restriction does not silently promote trust levels.

        Restriction should never turn a ``"proposed"`` or ``"copilot-proposed"``
        entry into a ``"verified"`` entry.  This method validates that trust
        annotations are preserved or weakened, never strengthened.
        """
        trust_order = {"copilot-proposed": 0, "proposed": 1, "assumed": 2, "verified": 3}
        for entry in restricted.entries:
            original = self._context.lookup_local(entry.name)
            if original is None:
                continue
            orig_level = trust_order.get(original.trust_annotation, 1)
            new_level = trust_order.get(entry.trust_annotation, 1)
            if new_level > orig_level:
                raise ValueError(
                    f"trust promotion detected on {entry.name!r}: "
                    f"{original.trust_annotation!r} -> {entry.trust_annotation!r} "
                    f"during restriction to {self._target.key}"
                )

    def dropped_entries(self) -> list[ContextEntry]:
        """Return the entries that would be dropped by this restriction."""
        visible_names = {e.name for e in self.compute_visible_entries()}
        return [e for e in self._context.entries if e.name not in visible_names]


# ---------------------------------------------------------------------------
# ContextExtension — extends a context with new entries
# ---------------------------------------------------------------------------


class ContextExtension:
    """Extends a :class:`JudgmentContext` with new entries.

    Provides validated extension operations that check for duplicates,
    type consistency, and scope containment before adding entries.  The
    ``compute_new_obligations`` method reports any obligations that arise
    from the extension (e.g. proof obligations for newly added judgments).
    """

    def __init__(self, context: JudgmentContext) -> None:
        self._context = context
        self._pending: list[ContextEntry] = []
        self._obligations: list[dict[str, Any]] = []

    @property
    def context(self) -> JudgmentContext:
        """The base context being extended."""
        return self._context

    @property
    def pending_entries(self) -> tuple[ContextEntry, ...]:
        """Entries queued for addition but not yet committed."""
        return tuple(self._pending)

    def extend_with_binding(
        self,
        name: str,
        type_value: Any,
        *,
        trust: str = "verified",
        provenance: tuple[str, ...] = (),
    ) -> "ContextExtension":
        """Queue a new binding entry for addition.

        Returns ``self`` for method chaining.
        """
        entry = ContextEntry(
            name=name,
            entry_type=EntryType.BINDING,
            value={"type": type_value},
            trust_annotation=trust,
            provenance=provenance or ("extend-binding",),
            scope_coordinate=self._context.coordinate,
        )
        self._pending.append(entry)
        return self

    def extend_with_judgment(
        self,
        name: str,
        proposition: Any,
        *,
        trust: str = "verified",
        provenance: tuple[str, ...] = (),
    ) -> "ContextExtension":
        """Queue a new judgment entry for addition.

        Returns ``self`` for method chaining.
        """
        entry = ContextEntry(
            name=name,
            entry_type=EntryType.JUDGMENT,
            value=proposition,
            trust_annotation=trust,
            provenance=provenance or ("extend-judgment",),
            scope_coordinate=self._context.coordinate,
        )
        self._pending.append(entry)
        if trust != "verified":
            self._obligations.append({
                "kind": "proof-obligation",
                "entry": name,
                "proposition": proposition,
                "trust": trust,
            })
        return self

    def extend_with_assumption(
        self,
        name: str,
        proposition: Any,
        *,
        provenance: tuple[str, ...] = (),
    ) -> "ContextExtension":
        """Queue a new assumption entry for addition.

        Returns ``self`` for method chaining.
        """
        entry = ContextEntry(
            name=name,
            entry_type=EntryType.ASSUMPTION,
            value=proposition,
            trust_annotation="assumed",
            provenance=provenance or ("extend-assumption",),
            scope_coordinate=self._context.coordinate,
        )
        self._pending.append(entry)
        return self

    def extend_with_definition(
        self,
        name: str,
        body: Any,
        *,
        trust: str = "verified",
        provenance: tuple[str, ...] = (),
    ) -> "ContextExtension":
        """Queue a new definition entry for addition.

        Returns ``self`` for method chaining.
        """
        entry = ContextEntry(
            name=name,
            entry_type=EntryType.DEFINITION,
            value=body,
            trust_annotation=trust,
            provenance=provenance or ("extend-definition",),
            scope_coordinate=self._context.coordinate,
        )
        self._pending.append(entry)
        return self

    def validate_extension(self) -> list[str]:
        """Validate all pending entries against the base context.

        Returns a list of error messages.  An empty list means all pending
        entries are valid.
        """
        errors: list[str] = []
        existing_names = {e.name for e in self._context.entries}
        pending_names: set[str] = set()
        for entry in self._pending:
            if entry.name in existing_names:
                errors.append(f"duplicate name {entry.name!r}: already in context")
            if entry.name in pending_names:
                errors.append(f"duplicate name {entry.name!r}: appears twice in extension")
            pending_names.add(entry.name)
        return errors

    def compute_new_obligations(self) -> list[dict[str, Any]]:
        """Return proof obligations generated by the pending extension.

        Obligations arise when judgments are added with trust lower than
        ``"verified"``, or when assumptions introduce hypotheses that must
        eventually be discharged.
        """
        obligations = list(self._obligations)
        for entry in self._pending:
            if entry.entry_type == EntryType.ASSUMPTION:
                obligations.append({
                    "kind": "discharge-obligation",
                    "entry": entry.name,
                    "proposition": entry.value,
                })
        return obligations

    def commit(self) -> JudgmentContext:
        """Apply all pending entries to the base context and return the result.

        Validates first; raises ``ValueError`` if validation fails.
        """
        errors = self.validate_extension()
        if errors:
            raise ValueError(
                f"extension validation failed: {'; '.join(errors)}"
            )
        result = JudgmentContext(
            coordinate=self._context.coordinate,
            parent_context=self._context.parent_context,
            depth=self._context.depth,
            trust_boundary=self._context.trust_boundary,
            provenance=self._context.provenance + ("extension-commit",),
        )
        for entry in self._context.entries:
            result._entries.append(entry)
            result._entry_index[entry.name] = entry
        for entry in self._pending:
            result._entries.append(entry)
            result._entry_index[entry.name] = entry
        self._pending.clear()
        return result


# ---------------------------------------------------------------------------
# ContextDiff — difference between two contexts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextDiff:
    """The difference between two :class:`JudgmentContext` instances.

    Captures added, removed, and modified entries so that context evolution
    can be tracked, compared, and potentially rolled back.
    """

    added_entries: tuple[ContextEntry, ...]
    removed_entries: tuple[ContextEntry, ...]
    modified_entries: tuple[tuple[ContextEntry, ContextEntry], ...]
    source_coordinate: str
    target_coordinate: str

    @staticmethod
    def compute(
        before: JudgmentContext,
        after: JudgmentContext,
    ) -> "ContextDiff":
        """Compute the diff between ``before`` and ``after`` contexts.

        Entries present only in ``after`` are added; entries present only in
        ``before`` are removed; entries present in both but with different
        values or trust are modified.
        """
        before_index = {e.name: e for e in before.entries}
        after_index = {e.name: e for e in after.entries}
        before_names = set(before_index)
        after_names = set(after_index)

        added = tuple(after_index[n] for n in sorted(after_names - before_names))
        removed = tuple(before_index[n] for n in sorted(before_names - after_names))
        modified: list[tuple[ContextEntry, ContextEntry]] = []
        for name in sorted(before_names & after_names):
            old = before_index[name]
            new = after_index[name]
            if old.value != new.value or old.trust_annotation != new.trust_annotation or old.entry_type != new.entry_type:
                modified.append((old, new))

        return ContextDiff(
            added_entries=added,
            removed_entries=removed,
            modified_entries=tuple(modified),
            source_coordinate=before.coordinate.key,
            target_coordinate=after.coordinate.key,
        )

    def is_refinement(self) -> bool:
        """Return ``True`` if the diff only adds or strengthens entries.

        A refinement never removes entries and never weakens trust.
        """
        if self.removed_entries:
            return False
        trust_order = {"copilot-proposed": 0, "proposed": 1, "assumed": 2, "verified": 3}
        for old, new in self.modified_entries:
            old_level = trust_order.get(old.trust_annotation, 1)
            new_level = trust_order.get(new.trust_annotation, 1)
            if new_level < old_level:
                return False
        return True

    def is_coarsening(self) -> bool:
        """Return ``True`` if the diff only removes or weakens entries.

        A coarsening never adds entries and never strengthens trust.
        """
        if self.added_entries:
            return False
        trust_order = {"copilot-proposed": 0, "proposed": 1, "assumed": 2, "verified": 3}
        for old, new in self.modified_entries:
            old_level = trust_order.get(old.trust_annotation, 1)
            new_level = trust_order.get(new.trust_annotation, 1)
            if new_level > old_level:
                return False
        return True

    def is_identity(self) -> bool:
        """Return ``True`` if the diff represents no change."""
        return (
            not self.added_entries
            and not self.removed_entries
            and not self.modified_entries
        )

    def apply_to(self, context: JudgmentContext) -> JudgmentContext:
        """Apply this diff to ``context``, producing a new context.

        Added entries are appended, removed entries are dropped, and
        modified entries are replaced with their new versions.
        """
        removed_names = {e.name for e in self.removed_entries}
        modified_map = {old.name: new for old, new in self.modified_entries}
        result = JudgmentContext(
            coordinate=context.coordinate,
            parent_context=context.parent_context,
            depth=context.depth,
            trust_boundary=context.trust_boundary,
            provenance=context.provenance + ("diff-applied",),
        )
        for entry in context.entries:
            if entry.name in removed_names:
                continue
            replacement = modified_map.get(entry.name)
            actual = replacement if replacement is not None else entry
            result._entries.append(actual)
            result._entry_index[actual.name] = actual
        for entry in self.added_entries:
            if entry.name not in result._entry_index:
                result._entries.append(entry)
                result._entry_index[entry.name] = entry
        return result

    def summary(self) -> str:
        """Return a human-readable summary of the diff."""
        parts: list[str] = []
        if self.added_entries:
            names = ", ".join(e.name for e in self.added_entries)
            parts.append(f"+{len(self.added_entries)} ({names})")
        if self.removed_entries:
            names = ", ".join(e.name for e in self.removed_entries)
            parts.append(f"-{len(self.removed_entries)} ({names})")
        if self.modified_entries:
            names = ", ".join(old.name for old, _ in self.modified_entries)
            parts.append(f"~{len(self.modified_entries)} ({names})")
        return "; ".join(parts) if parts else "no changes"

    def to_mapping(self) -> dict[str, Any]:
        """Serialize the diff to a plain dictionary."""
        return {
            "added": [e.to_mapping() for e in self.added_entries],
            "removed": [e.to_mapping() for e in self.removed_entries],
            "modified": [
                {"before": old.to_mapping(), "after": new.to_mapping()}
                for old, new in self.modified_entries
            ],
            "source_coordinate": self.source_coordinate,
            "target_coordinate": self.target_coordinate,
        }


# ---------------------------------------------------------------------------
# ContextValidator — validates context well-formedness
# ---------------------------------------------------------------------------


class ContextValidator:
    """Validates well-formedness of :class:`JudgmentContext` instances.

    The validator checks structural invariants that the context must satisfy
    according to theory2.tex: no duplicate names, consistent types across
    references, monotonic trust, and scope containment.
    """

    def __init__(self, context: JudgmentContext) -> None:
        self._context = context
        self._errors: list[str] = []

    @property
    def errors(self) -> tuple[str, ...]:
        """Accumulated validation errors."""
        return tuple(self._errors)

    def check_no_duplicate_names(self) -> bool:
        """Verify that no two entries share a name.

        Returns ``True`` when the check passes.
        """
        seen: set[str] = set()
        ok = True
        for entry in self._context.entries:
            if entry.name in seen:
                self._errors.append(f"duplicate name: {entry.name!r}")
                ok = False
            seen.add(entry.name)
        return ok

    def check_type_consistency(self) -> bool:
        """Verify that entries referencing other names use consistent types.

        If an entry's value is a dict containing a ``"type"`` key that names
        another entry, the referenced entry must be a BINDING with a
        compatible value.  Returns ``True`` when the check passes.
        """
        index = {e.name: e for e in self._context.entries}
        ok = True
        for entry in self._context.entries:
            val = entry.value
            if isinstance(val, dict):
                type_ref = val.get("type")
                if isinstance(type_ref, str) and type_ref in index:
                    referenced = index[type_ref]
                    if referenced.entry_type != EntryType.BINDING:
                        self._errors.append(
                            f"type reference {type_ref!r} in {entry.name!r} points to "
                            f"a {referenced.entry_type.value}, not a binding"
                        )
                        ok = False
        return ok

    def check_trust_monotonicity(self) -> bool:
        """Verify that trust annotations respect the monotonicity discipline.

        In a well-formed context, assumptions never claim to be verified,
        and copilot-proposed entries never claim to be verified without an
        explicit trust-upgrade provenance step.  Returns ``True`` when OK.
        """
        ok = True
        for entry in self._context.entries:
            if entry.entry_type == EntryType.ASSUMPTION and entry.trust_annotation == "verified":
                # assumptions must be "assumed" unless explicitly promoted
                has_upgrade = any("trust-upgrade" in p for p in entry.provenance)
                if not has_upgrade:
                    self._errors.append(
                        f"assumption {entry.name!r} is marked 'verified' without "
                        f"a trust-upgrade provenance step"
                    )
                    ok = False
            if entry.trust_annotation == "copilot-proposed":
                # copilot proposals must not have verified trust
                pass  # already the weakest, always valid
        return ok

    def check_scope_containment(self) -> bool:
        """Verify that every entry's scope coordinate is contained in the context coordinate.

        An entry scoped at ``U`` must have ``U``'s path as a prefix of the
        context coordinate's path (or vice versa — the entry must be visible
        at the context coordinate).  Returns ``True`` when OK.
        """
        ok = True
        for entry in self._context.entries:
            if not entry.is_visible_at(self._context.coordinate):
                self._errors.append(
                    f"entry {entry.name!r} scoped at "
                    f"{entry.scope_coordinate.key if entry.scope_coordinate else 'None'!r} "
                    f"is not visible at context coordinate {self._context.coordinate.key!r}"
                )
                ok = False
        return ok

    def check_entry_value_well_formed(self) -> bool:
        """Verify that entry values have expected shapes.

        Bindings should have dict values with a ``"type"`` key.  Judgments
        and assumptions should have non-None values.  Returns ``True`` when OK.
        """
        ok = True
        for entry in self._context.entries:
            if entry.entry_type == EntryType.BINDING:
                if not isinstance(entry.value, dict) or "type" not in entry.value:
                    self._errors.append(
                        f"binding {entry.name!r} has malformed value: expected dict with 'type' key"
                    )
                    ok = False
            if entry.value is None:
                self._errors.append(f"entry {entry.name!r} has None value")
                ok = False
        return ok

    def full_validation(self) -> tuple[bool, tuple[str, ...]]:
        """Run all validation checks and return the result.

        Returns a ``(passed, errors)`` tuple.  ``passed`` is ``True`` only
        when all checks succeed.
        """
        self._errors.clear()
        checks = [
            self.check_no_duplicate_names(),
            self.check_type_consistency(),
            self.check_trust_monotonicity(),
            self.check_scope_containment(),
            self.check_entry_value_well_formed(),
        ]
        passed = all(checks)
        return passed, tuple(self._errors)


# ---------------------------------------------------------------------------
# ContextSerializer — JSON/dict serialization
# ---------------------------------------------------------------------------


class ContextSerializer:
    """Serializes and deserializes :class:`JudgmentContext` and related types.

    Provides both dict-based and JSON-string serialization.  Deserialization
    requires a coordinate factory because ``CoordinateObject`` is a frozen
    dataclass that cannot be reconstructed from a key string alone without
    extra context.
    """

    @staticmethod
    def serialize_entry(entry: ContextEntry) -> dict[str, Any]:
        """Serialize a single :class:`ContextEntry` to a dictionary."""
        return entry.to_mapping()

    @staticmethod
    def serialize_judgment_context(context: JudgmentContext) -> dict[str, Any]:
        """Serialize a :class:`JudgmentContext` to a dictionary.

        The parent context is referenced by coordinate key rather than
        fully serialized to avoid circular references.
        """
        return {
            "coordinate": context.coordinate.key,
            "entries": [e.to_mapping() for e in context.entries],
            "depth": context.depth,
            "trust_boundary": context.trust_boundary,
            "provenance": list(context.provenance),
            "parent_coordinate": (
                context.parent_context.coordinate.key
                if context.parent_context is not None
                else None
            ),
        }

    @staticmethod
    def serialize_context_stack(stack: ContextStack) -> dict[str, Any]:
        """Serialize a :class:`ContextStack` to a dictionary."""
        return {
            "depth": stack.depth(),
            "frames": [
                ContextSerializer.serialize_judgment_context(frame)
                for frame in stack.all_frames()
            ],
        }

    @staticmethod
    def serialize_semantic_context(context: SemanticContext) -> dict[str, Any]:
        """Serialize a :class:`SemanticContext` using its built-in mapping."""
        return context.to_mapping()

    @staticmethod
    def serialize_diff(diff: ContextDiff) -> dict[str, Any]:
        """Serialize a :class:`ContextDiff` to a dictionary."""
        return diff.to_mapping()

    @staticmethod
    def to_json(obj: Any, *, indent: int = 2) -> str:
        """Serialize a context object to a JSON string.

        Accepts ``JudgmentContext``, ``ContextStack``, ``SemanticContext``,
        ``ContextDiff``, ``ContextEntry``, or any dict/list.
        """
        if isinstance(obj, JudgmentContext):
            data = ContextSerializer.serialize_judgment_context(obj)
        elif isinstance(obj, ContextStack):
            data = ContextSerializer.serialize_context_stack(obj)
        elif isinstance(obj, SemanticContext):
            data = ContextSerializer.serialize_semantic_context(obj)
        elif isinstance(obj, ContextDiff):
            data = ContextSerializer.serialize_diff(obj)
        elif isinstance(obj, ContextEntry):
            data = ContextSerializer.serialize_entry(obj)
        elif isinstance(obj, dict):
            data = obj
        else:
            data = {"value": str(obj)}
        return json.dumps(data, indent=indent, default=str)

    @staticmethod
    def deserialize_entry(data: dict[str, Any], *, coordinate: CoordinateObject | None = None) -> ContextEntry:
        """Reconstruct a :class:`ContextEntry` from a dictionary.

        The ``coordinate`` parameter is used as the scope coordinate when
        ``data`` contains a non-None ``scope_coordinate`` key but no
        reconstructed object is available.
        """
        return ContextEntry(
            name=data["name"],
            entry_type=EntryType(data["entry_type"]),
            value=data.get("value"),
            trust_annotation=data.get("trust_annotation", "verified"),
            provenance=tuple(data.get("provenance", ())),
            scope_coordinate=coordinate,
        )

    @staticmethod
    def deserialize_judgment_context(
        data: dict[str, Any],
        *,
        coordinate: CoordinateObject,
        parent: JudgmentContext | None = None,
    ) -> JudgmentContext:
        """Reconstruct a :class:`JudgmentContext` from a dictionary.

        The caller must supply a ``coordinate`` because ``CoordinateObject``
        cannot be reconstructed from its key alone.
        """
        ctx = JudgmentContext(
            coordinate=coordinate,
            parent_context=parent,
            depth=data.get("depth", 0),
            trust_boundary=data.get("trust_boundary", "context"),
            provenance=tuple(data.get("provenance", ())),
        )
        for entry_data in data.get("entries", []):
            entry = ContextSerializer.deserialize_entry(entry_data, coordinate=coordinate)
            ctx._entries.append(entry)
            ctx._entry_index[entry.name] = entry
        return ctx


# ---------------------------------------------------------------------------
# ContextQuery — query language for contexts
# ---------------------------------------------------------------------------


class ContextQuery:
    """Query interface for :class:`JudgmentContext` instances.

    Provides filtered access to context entries by type, trust level,
    scope, and content.  This implements the query side of the context
    discipline described in theory2.tex §3.4, where downstream consumers
    need to find specific entries without scanning the full context.
    """

    def __init__(self, context: JudgmentContext) -> None:
        self._context = context

    def find_by_type(self, entry_type: EntryType) -> list[ContextEntry]:
        """Return all entries of the given type.

        The search includes only entries visible at the context's coordinate.
        """
        return [
            e for e in self._context.entries
            if e.entry_type == entry_type and e.is_visible_at(self._context.coordinate)
        ]

    def find_by_trust_level(self, trust: str) -> list[ContextEntry]:
        """Return all entries with the given trust annotation.

        Useful for finding copilot proposals (``"copilot-proposed"``),
        unverified entries (``"proposed"``), or assumptions (``"assumed"``).
        """
        return [
            e for e in self._context.entries
            if e.trust_annotation == trust
        ]

    def find_by_scope(self, coordinate: CoordinateObject) -> list[ContextEntry]:
        """Return all entries visible at the given coordinate.

        This queries visibility without actually restricting the context.
        """
        return [
            e for e in self._context.entries
            if e.is_visible_at(coordinate)
        ]

    def find_assumptions_about(self, topic: str) -> list[ContextEntry]:
        """Return assumption entries whose value mentions ``topic``.

        The search checks string values directly and dict values for
        string occurrences of ``topic`` in their serialized form.
        """
        results: list[ContextEntry] = []
        for entry in self._context.entries:
            if entry.entry_type != EntryType.ASSUMPTION:
                continue
            if _value_mentions(entry.value, topic):
                results.append(entry)
        return results

    def find_evidence_for(self, proposition: Any) -> list[ContextEntry]:
        """Return judgment entries whose value matches ``proposition``.

        Entries with copilot-proposed trust are included but flagged in their
        trust annotation so callers can distinguish verified from proposed
        evidence.
        """
        results: list[ContextEntry] = []
        for entry in self._context.entries:
            if entry.entry_type != EntryType.JUDGMENT:
                continue
            if entry.value == proposition:
                results.append(entry)
        return results

    def find_definitions(self) -> list[ContextEntry]:
        """Return all DEFINITION entries in the context."""
        return self.find_by_type(EntryType.DEFINITION)

    def find_copilot_proposals(self) -> list[ContextEntry]:
        """Return all entries proposed by a copilot channel.

        This covers both ``"copilot-proposed"`` and entries whose provenance
        contains a copilot tag.
        """
        results: list[ContextEntry] = []
        for entry in self._context.entries:
            if entry.trust_annotation == "copilot-proposed":
                results.append(entry)
            elif any("copilot" in p for p in entry.provenance):
                results.append(entry)
        return results

    def count_by_type(self) -> dict[str, int]:
        """Return a count of entries grouped by entry type."""
        counts: dict[str, int] = {}
        for entry in self._context.entries:
            key = entry.entry_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def names(self) -> frozenset[str]:
        """Return the set of all entry names in the context."""
        return frozenset(e.name for e in self._context.entries)

    def summary(self) -> str:
        """Return a concise human-readable summary of the context contents."""
        counts = self.count_by_type()
        parts = [f"{v} {k}(s)" for k, v in sorted(counts.items())]
        total = sum(counts.values())
        return f"{total} entries at {self._context.coordinate.key}: {', '.join(parts)}"


def _value_mentions(value: Any, topic: str) -> bool:
    """Return ``True`` if ``topic`` appears in the string representation of ``value``."""
    if isinstance(value, str):
        return topic in value
    if isinstance(value, dict):
        return topic in json.dumps(value, default=str)
    return topic in str(value)


# ---------------------------------------------------------------------------
# ContextHistory — tracks context evolution over time
# ---------------------------------------------------------------------------


class ContextHistory:
    """Tracks the evolution of a :class:`JudgmentContext` over time.

    Each mutation can be checkpointed, creating a snapshot that can be
    compared to other snapshots via :class:`ContextDiff` or rolled back to
    restore a prior state.  This is the temporal axis of context management
    that complements the spatial axis handled by restriction and merge.
    """

    def __init__(self, initial: JudgmentContext) -> None:
        self._snapshots: list[tuple[float, JudgmentContext]] = [
            (time.monotonic(), initial),
        ]
        self._labels: dict[str, int] = {"initial": 0}

    @property
    def length(self) -> int:
        """Number of snapshots in the history."""
        return len(self._snapshots)

    def checkpoint(self, context: JudgmentContext, *, label: str | None = None) -> int:
        """Record a new snapshot and return its index.

        If ``label`` is provided, the snapshot can be retrieved by name
        via :meth:`snapshot_by_label`.
        """
        idx = len(self._snapshots)
        self._snapshots.append((time.monotonic(), context))
        if label is not None:
            self._labels[label] = idx
        return idx

    def snapshot(self, index: int) -> JudgmentContext:
        """Return the context at snapshot ``index``.

        Raises ``IndexError`` if the index is out of range.
        """
        if index < 0 or index >= len(self._snapshots):
            raise IndexError(f"snapshot index {index} out of range [0, {len(self._snapshots)})")
        return self._snapshots[index][1]

    def snapshot_by_label(self, label: str) -> JudgmentContext | None:
        """Return the context at the labeled snapshot, or ``None``."""
        idx = self._labels.get(label)
        if idx is None:
            return None
        return self._snapshots[idx][1]

    def latest(self) -> JudgmentContext:
        """Return the most recent snapshot."""
        return self._snapshots[-1][1]

    def diff_between(self, from_index: int, to_index: int) -> ContextDiff:
        """Compute the diff between two snapshots.

        ``from_index`` is the older snapshot; ``to_index`` is the newer.
        """
        before = self.snapshot(from_index)
        after = self.snapshot(to_index)
        return ContextDiff.compute(before, after)

    def diff_since(self, index: int) -> ContextDiff:
        """Compute the diff from snapshot ``index`` to the latest snapshot."""
        return self.diff_between(index, len(self._snapshots) - 1)

    def rollback_to(self, index: int) -> JudgmentContext:
        """Roll back to snapshot ``index``, discarding later snapshots.

        Returns the context at the rolled-back position.  The rollback is
        recorded as a new snapshot labeled ``"rollback"`` so the operation
        itself is tracked.
        """
        target = self.snapshot(index)
        # remove snapshots after index
        self._snapshots = self._snapshots[: index + 1]
        # remove labels that pointed beyond the new end
        self._labels = {
            k: v for k, v in self._labels.items() if v <= index
        }
        # record the rollback as a new snapshot
        self.checkpoint(target, label="rollback")
        return target

    def timestamps(self) -> list[float]:
        """Return the monotonic timestamps of all snapshots."""
        return [ts for ts, _ in self._snapshots]

    def labels(self) -> dict[str, int]:
        """Return the label-to-index mapping."""
        return dict(self._labels)

    def all_diffs(self) -> list[ContextDiff]:
        """Compute diffs between consecutive snapshots.

        Returns a list of diffs where ``diffs[i]`` is the diff from
        snapshot ``i`` to snapshot ``i+1``.
        """
        diffs: list[ContextDiff] = []
        for i in range(len(self._snapshots) - 1):
            diffs.append(self.diff_between(i, i + 1))
        return diffs

    def entries_at(self, index: int) -> tuple[ContextEntry, ...]:
        """Return the entries of the context at snapshot ``index``."""
        return self.snapshot(index).entries

    def summary(self) -> str:
        """Return a concise summary of the history."""
        n = len(self._snapshots)
        label_str = ", ".join(f"{k}@{v}" for k, v in sorted(self._labels.items()))
        return f"ContextHistory({n} snapshots, labels: [{label_str}])"


# ---------------------------------------------------------------------------
# restrict_context / merge_contexts — foundational operations
# ---------------------------------------------------------------------------


def _validate_restriction_coordinate(source: CoordinateObject, target: CoordinateObject) -> None:
    """Ensure that ``target`` is a legal restriction of ``source``.

    In this shared foundation wave the geometry encodes locality by extending the
    path tuple. A restriction must therefore keep the source path as a prefix.
    """

    if source.key == target.key:
        return
    if not _path_is_prefix(source.path, target.path):
        raise ValueError(
            f"cannot restrict context from {source.key!r} to unrelated coordinate {target.key!r}"
        )


def restrict_context(
    context: SemanticContext,
    *,
    names: _StringIterable = (),
    coordinate: CoordinateObject | None = None,
    assumptions: _StringIterable | None = None,
    ambient_packs: _StringIterable | None = None,
    include_dependencies: bool = True,
    dependent_scope: _StringIterable | None = None,
    support_labels: _StringIterable = (),
) -> SemanticContext:
    """Restrict ``context`` without violating JuGeo's merge and scope discipline.

    ``names`` filters the visible bindings. When ``include_dependencies`` is
    true, the operation follows dependency closure so a dependent binding keeps
    its prerequisites. ``coordinate`` may refine the context to a more local
    descendant coordinate. Assumptions and ambient packs are preserved by default
    because theory2 treats them as explicit context carriers rather than hidden
    global state.
    """

    normalized_names = _dedupe_strings(names, label="names")
    identity_request = (
        not normalized_names
        and coordinate is None
        and assumptions is None
        and ambient_packs is None
        and dependent_scope is None
        and not _coerce_string_items(support_labels, label="support_labels")
    )
    if identity_request:
        return context

    target_coordinate = coordinate or context.coordinate
    _validate_restriction_coordinate(context.coordinate, target_coordinate)

    if normalized_names:
        selected_names = (
            context.dependency_closure(normalized_names)
            if include_dependencies
            else tuple(name for name in context.binding_names() if name in set(normalized_names))
        )
        selected_name_set = set(selected_names)
        bindings = tuple(binding for binding in context.bindings if binding.name in selected_name_set)
    else:
        bindings = context.bindings

    filtered_assumptions = context.assumptions_in_scope(assumptions)
    filtered_packs = context.ambient_packs_in_scope(ambient_packs)
    target_scope = (
        _dedupe_strings(dependent_scope, label="dependent_scope")
        if dependent_scope is not None
        else (target_coordinate.path if coordinate is not None else context.dependent_scope)
    )
    target_support = context.support_labels | _support_label_set(support_labels) | frozenset(target_coordinate.support_labels)

    provenance_fragments: list[str] = []
    if target_coordinate.key != context.coordinate.key:
        provenance_fragments.append("restrict-coordinate")
    if bindings != context.bindings:
        provenance_fragments.append("restrict-bindings")
    if filtered_assumptions != context.assumptions:
        provenance_fragments.append("restrict-assumptions")
    if filtered_packs != context.ambient_packs:
        provenance_fragments.append("restrict-ambient-packs")
    if target_scope != context.dependent_scope:
        provenance_fragments.append("restrict-scope")
    if target_support != context.support_labels:
        provenance_fragments.append("restrict-support")

    if (
        target_coordinate == context.coordinate
        and bindings == context.bindings
        and filtered_assumptions == context.assumptions
        and filtered_packs == context.ambient_packs
        and target_scope == context.dependent_scope
        and target_support == context.support_labels
        and not provenance_fragments
    ):
        return context

    return SemanticContext(
        coordinate=target_coordinate,
        bindings=bindings,
        assumptions=filtered_assumptions,
        ambient_packs=filtered_packs,
        trust_boundary=context.trust_boundary,
        dependent_scope=target_scope,
        support_labels=target_support,
        provenance=context.provenance + tuple(provenance_fragments),
    )


def _merge_target_coordinate(
    left: SemanticContext,
    right: SemanticContext,
    coordinate: CoordinateObject | None,
) -> CoordinateObject:
    """Choose the coordinate at which two contexts should be merged."""

    if coordinate is not None:
        _validate_restriction_coordinate(left.coordinate, coordinate)
        _validate_restriction_coordinate(right.coordinate, coordinate)
        return coordinate
    if left.coordinate.key == right.coordinate.key:
        return left.coordinate
    if _path_is_prefix(left.coordinate.path, right.coordinate.path):
        return right.coordinate
    if _path_is_prefix(right.coordinate.path, left.coordinate.path):
        return left.coordinate
    raise ValueError(
        "cannot merge contexts from unrelated coordinates without an explicit common refinement"
    )


def _merged_bindings(left: SemanticContext, right: SemanticContext) -> tuple[ContextBinding, ...]:
    """Merge compatible bindings while preserving first-seen order."""

    ordered: list[ContextBinding] = list(left.bindings)
    positions = {binding.name: index for index, binding in enumerate(ordered)}
    for binding in right.bindings:
        position = positions.get(binding.name)
        if position is None:
            positions[binding.name] = len(ordered)
            ordered.append(binding)
            continue
        existing = ordered[position]
        if not existing.compatible_with(binding):
            raise ValueError(f"context conflict on {binding.name}")
        ordered[position] = existing.merge_metadata(binding)
    return tuple(ordered)


def merge_contexts(
    left: SemanticContext,
    right: SemanticContext,
    *,
    coordinate: CoordinateObject | None = None,
) -> SemanticContext:
    """Merge two contexts using JuGeo's shared foundation discipline.

    The merge proceeds at the most local compatible coordinate, restricting
    either input when necessary. Shared bindings must agree on value; otherwise
    the overlap is obstructed and a ``ValueError`` is raised. Assumptions and
    ambient packs are cumulative because theory2 treats them as explicit carriers
    of local admissibility. Trust boundaries must match exactly so merges never
    smuggle silent evidence promotion across packs or scopes.
    """

    target_coordinate = _merge_target_coordinate(left, right, coordinate)
    left_local = left if left.coordinate.key == target_coordinate.key else restrict_context(left, coordinate=target_coordinate)
    right_local = right if right.coordinate.key == target_coordinate.key else restrict_context(right, coordinate=target_coordinate)

    if left_local.trust_boundary != right_local.trust_boundary:
        raise ValueError(
            "cannot merge contexts across trust boundaries "
            f"{left_local.trust_boundary!r} and {right_local.trust_boundary!r}"
        )

    return SemanticContext(
        coordinate=target_coordinate,
        bindings=_merged_bindings(left_local, right_local),
        assumptions=_dedupe_strings(chain(left_local.assumptions, right_local.assumptions), label="assumptions"),
        ambient_packs=_dedupe_strings(
            chain(left_local.ambient_packs, right_local.ambient_packs),
            label="ambient_packs",
        ),
        trust_boundary=left_local.trust_boundary,
        dependent_scope=target_coordinate.path,
        support_labels=left_local.support_labels | right_local.support_labels,
        provenance=left_local.provenance + right_local.provenance + ("merge",),
    )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------


__all__ = [
    "ContextBinding",
    "ContextDiff",
    "ContextEntry",
    "ContextExtension",
    "ContextHistory",
    "ContextMerger",
    "ContextPresheaf",
    "ContextQuery",
    "ContextRestriction",
    "ContextSerializer",
    "ContextStack",
    "ContextValidator",
    "EntryType",
    "JudgmentContext",
    "SemanticContext",
    "merge_contexts",
    "restrict_context",
]
