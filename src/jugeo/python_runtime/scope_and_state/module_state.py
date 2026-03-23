"""Section 4 — Module State as a Section Over Module Coordinate (theory2.tex Ch15 §4).

In Ch15, the state of a Python module — the set of globally-bound names and
their current values — is modelled as a *section* over the module coordinate.
Concretely, if ``M`` is the module coordinate in the semantic site, a section
``σ : M → Names × Types`` assigns a type representation to every globally
visible name at a given instant.  This section *evolves* over time: every
``import`` statement, top-level assignment, or ``del`` statement transforms
``σ`` into a new section ``σ'``.

The sheaf perspective imposes a **consistency condition**: any two observers
that hold a local view of the module state (e.g. two call sites that have each
captured a reference to the module's ``__dict__``) must agree on the values of
names that are in the intersection of their views.  Disagreement signals a
coherence failure that the :class:`ModuleStateValidator` can detect.

The :class:`ModuleStateTracker` is the central mutable object in this section;
it acts as the *stalk* functor evaluated at the module coordinate, accumulating
the section's evolution as a versioned sequence of
:class:`ModuleStateSnapshot` objects.

All copilot-generated; see theory2.tex Ch15 §4 for the formal development.
"""

from __future__ import annotations

import logging
import time
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
# ModuleStateSnapshot  (frozen — immutable value object)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModuleStateSnapshot:
    """Point-in-time snapshot of a module's global namespace.

    Captures the full set of globally-bound names and their type
    representations at a specific instant.  Snapshots are immutable; to
    update the module state callers must produce a new snapshot via
    :class:`ModuleStateTracker`.

    In the sheaf model (theory2.tex Ch15 §4), each snapshot corresponds to
    a particular section ``σ_v`` indexed by version ``v``.

    Parameters:
        module_name: Dotted module name, e.g. ``"mypackage.mymodule"``.
        timestamp: POSIX timestamp when the snapshot was taken.
        bindings: Tuple of ``(name, type_repr)`` pairs representing all
            globally-bound names at this version.
        version: Monotonically increasing integer epoch counter.

    Returns:
        Immutable snapshot descriptor.

    Example::

        snap = ModuleStateSnapshot(
            module_name="mypackage.mymodule",
            timestamp=time.time(),
            bindings=(("Counter", "type"), ("LIMIT", "int")),
            version=3,
        )
    """

    module_name: str
    timestamp: float
    bindings: tuple[tuple[str, str], ...]
    version: int = 0

    # ------------------------------------------------------------------
    # Comparison / diff
    # ------------------------------------------------------------------

    def diff(self, other: ModuleStateSnapshot) -> dict[str, Any]:
        """Compute the structural difference between *self* and *other*.

        Parameters:
            other: Another snapshot of the same module (possibly a later
                version).

        Returns:
            Dict with three keys:

            * ``"added"`` — names present in *other* but not *self*.
            * ``"removed"`` — names present in *self* but not *other*.
            * ``"changed"`` — names present in both but with different
              type representations.  Each entry is a ``(old_repr, new_repr)``
              pair.
        """
        self_map: dict[str, str] = dict(self.bindings)
        other_map: dict[str, str] = dict(other.bindings)

        self_names = set(self_map)
        other_names = set(other_map)

        added = sorted(other_names - self_names)
        removed = sorted(self_names - other_names)
        changed: dict[str, tuple[str, str]] = {
            name: (self_map[name], other_map[name])
            for name in self_names & other_names
            if self_map[name] != other_map[name]
        }
        return {
            "added": added,
            "removed": removed,
            "changed": changed,
        }

    def serialize(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns:
            JSON-compatible dict with all snapshot fields.
        """
        return {
            "module_name": self.module_name,
            "timestamp": self.timestamp,
            "bindings": [list(pair) for pair in self.bindings],
            "version": self.version,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ModuleStateSnapshot:
        """Reconstruct from a serialised dictionary.

        Parameters:
            data: Dictionary as produced by :meth:`serialize`.

        Returns:
            A freshly constructed :class:`ModuleStateSnapshot`.

        Raises:
            KeyError: If required keys are absent.
            TypeError: If ``bindings`` items cannot be unpacked as pairs.
        """
        return cls(
            module_name=data["module_name"],
            timestamp=float(data["timestamp"]),
            bindings=tuple(
                (str(pair[0]), str(pair[1]))
                for pair in data.get("bindings", [])
            ),
            version=int(data.get("version", 0)),
        )

    def has_name(self, name: str) -> bool:
        """Return ``True`` if *name* is present in this snapshot.

        Parameters:
            name: Bare identifier string.

        Returns:
            Boolean.
        """
        return any(n == name for n, _ in self.bindings)

    def get_binding(self, name: str) -> str | None:
        """Return the type representation for *name*, or ``None``.

        Parameters:
            name: Bare identifier string.

        Returns:
            Type representation string, or ``None`` if the name is absent.
        """
        for n, t in self.bindings:
            if n == name:
                return t
        return None

    def binding_count(self) -> int:
        """Return the number of globally-bound names in this snapshot.

        Returns:
            Non-negative integer.
        """
        return len(self.bindings)


# ---------------------------------------------------------------------------
# ModuleStateTracker  (mutable — KEY CLASS)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ModuleStateTracker:
    """Tracks the evolution of a module's global namespace over time.

    Maintains a mutable view of the current global bindings (``_current``)
    and an ordered log of :class:`ModuleStateSnapshot` objects capturing the
    state at each version epoch.  The tracker is the primary *stalk* object
    for the module coordinate in the sheaf model.

    Use :meth:`snapshot` to freeze the current state and advance the version
    counter.  Use :meth:`restore_snapshot` to roll back to a previous epoch.

    Parameters:
        module_name: Dotted module name, e.g. ``"mypackage.mymodule"``.

    Example::

        tracker = ModuleStateTracker(module_name="mypackage.mymodule")
        tracker.record_assignment("Counter", "type")
        tracker.record_import("collections", ["OrderedDict"])
        snap = tracker.snapshot()
    """

    module_name: str
    _current: dict[str, str] = field(default_factory=dict)
    _version: int = field(default=0)
    _snapshots: list[ModuleStateSnapshot] = field(default_factory=list)
    _import_log: list[dict[str, Any]] = field(default_factory=list)
    _deletion_log: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Mutation operations
    # ------------------------------------------------------------------

    def record_assignment(self, name: str, type_repr: str) -> None:
        """Add or update a global binding.

        Increments the version counter whenever a name is first added
        (not on updates to an existing name, which are tracked via
        :meth:`snapshot`).

        Parameters:
            name: Bare identifier string, e.g. ``"Counter"``.
            type_repr: Type representation string, e.g. ``"type"``.

        Returns:
            None.
        """
        is_new = name not in self._current
        self._current[name] = type_repr
        if is_new:
            logger.debug(
                "Module %s: new global binding '%s: %s'",
                self.module_name,
                name,
                type_repr,
            )
        else:
            logger.debug(
                "Module %s: updated binding '%s' → '%s'",
                self.module_name,
                name,
                type_repr,
            )

    def record_import(self, module: str, names: list[str]) -> None:
        """Record that *names* were imported from *module*.

        Each imported name is added as a global binding with a type
        representation of ``f"<imported from {module}>"``.

        Parameters:
            module: The source module name, e.g. ``"collections"``.
            names: List of bare names being imported, e.g.
                ``["OrderedDict", "defaultdict"]``.

        Returns:
            None.
        """
        event: dict[str, Any] = {
            "kind": "import",
            "module": module,
            "names": list(names),
            "timestamp": time.time(),
            "version_before": self._version,
        }
        self._import_log.append(event)
        for name in names:
            self.record_assignment(name, f"<imported from {module}>")
        logger.debug(
            "Module %s: imported %s from '%s'",
            self.module_name,
            names,
            module,
        )

    def record_deletion(self, name: str) -> bool:
        """Remove a global binding by name.

        Parameters:
            name: Bare identifier string to delete.

        Returns:
            ``True`` if the name was present and removed; ``False`` if it
            was not found.
        """
        if name not in self._current:
            logger.warning(
                "Module %s: tried to delete absent name '%s'",
                self.module_name,
                name,
            )
            return False
        type_repr = self._current.pop(name)
        event: dict[str, Any] = {
            "name": name,
            "type_repr": type_repr,
            "timestamp": time.time(),
            "version": self._version,
        }
        self._deletion_log.append(event)
        logger.debug(
            "Module %s: deleted global binding '%s'",
            self.module_name,
            name,
        )
        return True

    # ------------------------------------------------------------------
    # Snapshotting
    # ------------------------------------------------------------------

    def snapshot(self) -> ModuleStateSnapshot:
        """Capture the current module state as an immutable snapshot.

        Advances the internal version counter by 1, then freezes the current
        ``_current`` dict into a :class:`ModuleStateSnapshot`.  The snapshot
        is appended to ``_snapshots`` for later retrieval.

        Returns:
            The newly created :class:`ModuleStateSnapshot`.
        """
        self._version += 1
        snap = ModuleStateSnapshot(
            module_name=self.module_name,
            timestamp=time.time(),
            bindings=tuple(sorted(self._current.items())),
            version=self._version,
        )
        self._snapshots.append(snap)
        logger.debug(
            "Module %s: snapshot v%d (%d bindings)",
            self.module_name,
            self._version,
            snap.binding_count(),
        )
        return snap

    def restore_snapshot(self, version: int) -> bool:
        """Restore the module state to a previous snapshot version.

        Overwrites ``_current`` with the bindings from the snapshot whose
        ``version`` field equals *version*.  Does **not** advance the version
        counter; that happens on the next call to :meth:`snapshot`.

        Parameters:
            version: The target version number to restore.

        Returns:
            ``True`` if a matching snapshot was found and applied;
            ``False`` otherwise.
        """
        for snap in self._snapshots:
            if snap.version == version:
                self._current = dict(snap.bindings)
                logger.info(
                    "Module %s: restored to v%d",
                    self.module_name,
                    version,
                )
                return True
        logger.warning(
            "Module %s: no snapshot found for version %d",
            self.module_name,
            version,
        )
        return False

    def diff_snapshots(self, v1: int, v2: int) -> dict[str, Any]:
        """Compute the diff between two snapshot versions.

        Parameters:
            v1: Earlier version number.
            v2: Later version number.

        Returns:
            Dict as returned by :meth:`ModuleStateSnapshot.diff`, or an error
            dict if either version is not found.
        """
        snap1: ModuleStateSnapshot | None = None
        snap2: ModuleStateSnapshot | None = None
        for snap in self._snapshots:
            if snap.version == v1:
                snap1 = snap
            if snap.version == v2:
                snap2 = snap
        if snap1 is None or snap2 is None:
            missing = [v for v, s in [(v1, snap1), (v2, snap2)] if s is None]
            return {"error": f"Version(s) not found: {missing}"}
        return snap1.diff(snap2)

    # ------------------------------------------------------------------
    # Manifest & judgment
    # ------------------------------------------------------------------

    def build_state_manifest(
        self, module_coord: CoordinateObject
    ) -> ModuleStateManifest:
        """Construct a :class:`ModuleStateManifest` from the current state.

        Parameters:
            module_coord: The :class:`CoordinateObject` for this module in the
                semantic site.

        Returns:
            A :class:`ModuleStateManifest` reflecting the current bindings and
            version.
        """
        return ModuleStateManifest(
            module_name=self.module_name,
            module_coordinate=module_coord,
            global_names=tuple(sorted(self._current)),
            type_reprs=dict(self._current),
            version=self._version,
            metadata={
                "snapshot_count": len(self._snapshots),
                "import_count": len(self._import_log),
                "deletion_count": len(self._deletion_log),
            },
        )

    def validate_state(self) -> list[str]:
        """Check the current module state for invalid conditions.

        Checks performed:

        1. No name maps to an empty type representation.
        2. No name is ``None`` or the empty string.
        3. Version counter is non-negative.
        4. Snapshots are ordered by version (monotonically increasing).

        Returns:
            List of human-readable violation strings.  Empty list means the
            state is valid.
        """
        violations: list[str] = []
        for name, type_repr in self._current.items():
            if not name:
                violations.append("Empty name key in _current bindings")
            if not type_repr:
                violations.append(
                    f"Name '{name}' has an empty type representation"
                )
        if self._version < 0:
            violations.append(
                f"Version counter is negative: {self._version}"
            )
        versions = [s.version for s in self._snapshots]
        for i in range(1, len(versions)):
            if versions[i] <= versions[i - 1]:
                violations.append(
                    f"Snapshot versions not strictly increasing at index {i}: "
                    f"{versions[i - 1]} then {versions[i]}"
                )
        return violations

    def build_state_judgment(
        self, module_coord: CoordinateObject
    ) -> Judgment:
        """Build a :class:`Judgment` asserting module state consistency.

        The judgment has :attr:`PropositionKind.STRUCTURAL` and states that
        the current section over ``module_coord`` is consistent: every name
        in the global namespace has a well-defined type representation.

        Parameters:
            module_coord: The module's :class:`CoordinateObject`.

        Returns:
            A :class:`Judgment` with trust :attr:`TrustLevel.UNVERIFIED`.
        """
        violations = self.validate_state()
        name_count = len(self._current)
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=(
                f"module_state_consistent({self.module_name}) ∧ "
                f"|global_names| = {name_count} ∧ "
                f"∀ name ∈ global_names . type_repr(name) ≠ ∅"
            ),
            free_variables=tuple(sorted(self._current)),
        )
        carrier = Carrier(name="ModuleStateCarrier")
        trust_level = (
            TrustLevel.UNVERIFIED if violations else TrustLevel.ORACLE_PROPOSED
        )
        trust = TrustAnnotation(level=trust_level)
        return Judgment(
            coordinate=module_coord,
            proposition=prop,
            carrier=carrier,
            trust=trust,
        )

    def current_bindings(self) -> dict[str, str]:
        """Return a copy of the current name-to-type-repr mapping.

        Returns:
            Shallow copy of the internal ``_current`` dict.
        """
        return dict(self._current)


# ---------------------------------------------------------------------------
# GlobalNameTracker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GlobalNameTracker:
    """Tracks global names as they are added and removed over time.

    Provides a lightweight alternative to the full :class:`ModuleStateTracker`
    when only *name membership* (not type information) is needed.  Records
    POSIX timestamps for each add/remove event so that temporal queries are
    possible.

    Parameters:
        module_name: Dotted module name.

    Example::

        tracker = GlobalNameTracker(module_name="mypackage.mymodule")
        tracker.add_name("Counter")
        tracker.add_name("LIMIT")
        names_after_import = tracker.names_added_since(t0)
    """

    module_name: str
    _names: set[str] = field(default_factory=set)
    _added_at: dict[str, float] = field(default_factory=dict)
    _removed_at: dict[str, float] = field(default_factory=dict)

    def add_name(self, name: str) -> None:
        """Register *name* as a globally-visible identifier.

        If *name* is already present, the call is a no-op (no timestamp
        update).

        Parameters:
            name: Bare identifier string.

        Returns:
            None.
        """
        if name not in self._names:
            self._names.add(name)
            self._added_at[name] = time.time()
            # Remove from removed_at if it was previously removed
            self._removed_at.pop(name, None)
            logger.debug(
                "GlobalNameTracker [%s]: added '%s'", self.module_name, name
            )

    def remove_name(self, name: str) -> bool:
        """Remove *name* from the globally-visible namespace.

        Parameters:
            name: Bare identifier string.

        Returns:
            ``True`` if *name* was present and removed; ``False`` otherwise.
        """
        if name not in self._names:
            return False
        self._names.discard(name)
        self._removed_at[name] = time.time()
        logger.debug(
            "GlobalNameTracker [%s]: removed '%s'", self.module_name, name
        )
        return True

    def rename(self, old_name: str, new_name: str) -> bool:
        """Atomically remove *old_name* and add *new_name*.

        Parameters:
            old_name: Existing name to retire.
            new_name: Replacement name.

        Returns:
            ``True`` if *old_name* was found and the rename succeeded;
            ``False`` if *old_name* was not present.
        """
        if old_name not in self._names:
            logger.warning(
                "GlobalNameTracker [%s]: rename failed — '%s' not found",
                self.module_name,
                old_name,
            )
            return False
        self.remove_name(old_name)
        self.add_name(new_name)
        return True

    def all_names(self) -> list[str]:
        """Return a sorted list of all currently present global names.

        Returns:
            Sorted list of bare identifier strings.
        """
        return sorted(self._names)

    def names_added_since(self, timestamp: float) -> list[str]:
        """Return names added at or after *timestamp*.

        Parameters:
            timestamp: POSIX timestamp threshold.

        Returns:
            Sorted list of names whose add timestamp is ≥ *timestamp*.
        """
        return sorted(
            name
            for name, ts in self._added_at.items()
            if ts >= timestamp and name in self._names
        )

    def names_removed_since(self, timestamp: float) -> list[str]:
        """Return names removed at or after *timestamp*.

        Parameters:
            timestamp: POSIX timestamp threshold.

        Returns:
            Sorted list of names whose removal timestamp is ≥ *timestamp*.
        """
        return sorted(
            name
            for name, ts in self._removed_at.items()
            if ts >= timestamp
        )

    def current_state(self) -> set[str]:
        """Return a copy of the current name set.

        Returns:
            Shallow copy of ``_names``.
        """
        return set(self._names)

    def has_name(self, name: str) -> bool:
        """Return ``True`` if *name* is currently present.

        Parameters:
            name: Bare identifier string.

        Returns:
            Boolean.
        """
        return name in self._names

    def count(self) -> int:
        """Return the number of currently present names.

        Returns:
            Non-negative integer.
        """
        return len(self._names)


# ---------------------------------------------------------------------------
# ImportTracker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ImportTracker:
    """Tracks import statements and their effects on the global namespace.

    Records every ``import`` and ``from ... import`` encountered in the
    module's top-level execution.  Also detects ``from foo import *`` (star
    imports), which can silently pollute the namespace and violate the
    monotonicity property analysed by :class:`ModuleStateValidator`.

    Parameters:
        module_name: Dotted name of the module being analysed.

    Example::

        tracker = ImportTracker(module_name="mypackage.mymodule")
        tracker.record_import("os")
        tracker.record_from_import("collections", [("OrderedDict", None)])
        tracker.record_star_import("typing")
        print(tracker.imported_names())  # ["OrderedDict", "os"]
    """

    module_name: str
    _imports: list[dict[str, Any]] = field(default_factory=list)
    _by_source: dict[str, list[str]] = field(default_factory=dict)

    def record_import(
        self, module: str, alias: str | None = None
    ) -> None:
        """Record a bare ``import foo`` or ``import foo as bar`` statement.

        The locally-bound name is *alias* if given, otherwise the top-level
        component of *module* (e.g. ``"os"`` for ``import os.path``).

        Parameters:
            module: The fully-qualified module name being imported.
            alias: Optional local alias (``as`` name).

        Returns:
            None.
        """
        local_name = alias if alias else module.split(".")[0]
        event: dict[str, Any] = {
            "kind": "import",
            "module": module,
            "alias": alias,
            "local_name": local_name,
            "timestamp": time.time(),
        }
        self._imports.append(event)
        self._by_source.setdefault(module, [])
        if local_name not in self._by_source[module]:
            self._by_source[module].append(local_name)
        logger.debug(
            "ImportTracker [%s]: import %s as %s",
            self.module_name,
            module,
            local_name,
        )

    def record_from_import(
        self,
        module: str,
        names: list[tuple[str, str | None]],
    ) -> None:
        """Record a ``from foo import bar, baz as qux`` statement.

        Parameters:
            module: The source module, e.g. ``"collections"``.
            names: List of ``(original_name, alias)`` pairs.  ``alias`` is
                ``None`` when no ``as`` clause is used.

        Returns:
            None.
        """
        local_names: list[str] = []
        for original, alias in names:
            local_name = alias if alias else original
            local_names.append(local_name)
            event: dict[str, Any] = {
                "kind": "from_import",
                "module": module,
                "original": original,
                "alias": alias,
                "local_name": local_name,
                "timestamp": time.time(),
            }
            self._imports.append(event)
        self._by_source.setdefault(module, [])
        for local_name in local_names:
            if local_name not in self._by_source[module]:
                self._by_source[module].append(local_name)
        logger.debug(
            "ImportTracker [%s]: from %s import %s",
            self.module_name,
            module,
            local_names,
        )

    def record_star_import(self, module: str) -> None:
        """Record a ``from foo import *`` statement.

        Star imports are tracked separately because they introduce an
        indeterminate set of names into the global namespace — this is flagged
        as a potential consistency violation by :class:`ModuleStateValidator`.

        Parameters:
            module: The source module, e.g. ``"typing"``.

        Returns:
            None.
        """
        event: dict[str, Any] = {
            "kind": "star_import",
            "module": module,
            "timestamp": time.time(),
        }
        self._imports.append(event)
        self._by_source.setdefault(module, [])
        if "*" not in self._by_source[module]:
            self._by_source[module].append("*")
        logger.warning(
            "ImportTracker [%s]: star import from '%s' detected",
            self.module_name,
            module,
        )

    def imported_names(self) -> list[str]:
        """Return all locally-bound names introduced by import statements.

        Star imports contribute the placeholder ``"*"`` rather than
        individual names (since individual names are not statically
        determinable).

        Returns:
            Sorted list of local name strings.
        """
        names: set[str] = set()
        for event in self._imports:
            local = event.get("local_name")
            if local:
                names.add(local)
            elif event.get("kind") == "star_import":
                names.add("*")
        return sorted(names)

    def import_sources(self) -> list[str]:
        """Return a sorted list of all source modules referenced.

        Returns:
            Sorted list of dotted module name strings.
        """
        return sorted(self._by_source)

    def build_import_graph(self) -> dict[str, list[str]]:
        """Build a mapping from source module to list of imported local names.

        Returns:
            Dict where each key is a source module name and each value is a
            sorted list of the locally-bound names introduced from that source.
        """
        return {
            module: sorted(set(names))
            for module, names in self._by_source.items()
        }

    def has_star_import(self) -> bool:
        """Return ``True`` if any star import has been recorded.

        Returns:
            Boolean.
        """
        return any(e.get("kind") == "star_import" for e in self._imports)

    def serialize(self) -> dict[str, Any]:
        """Serialise the tracker's state to a plain dictionary.

        Returns:
            JSON-compatible dict with ``"module_name"``, ``"imports"``,
            and ``"by_source"`` keys.
        """
        return {
            "module_name": self.module_name,
            "imports": list(self._imports),
            "by_source": {
                module: list(names)
                for module, names in self._by_source.items()
            },
        }


# ---------------------------------------------------------------------------
# ModuleStateValidator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ModuleStateValidator:
    """Validates module state against sheaf-theoretic invariants.

    Performs three categories of checks:

    1. **Section validity** — the manifest's coordinate is non-empty and
       the global name list is non-trivially populated.
    2. **Consistency** — two manifests that represent the same module at
       different times agree on names they share (no type-repr conflicts).
    3. **Monotonicity** — the version sequence of a snapshot history is
       strictly increasing.

    In the sheaf formalism (theory2.tex Ch15 §4), failures in (2) are
    precisely the coherence failures that prevent a local section from
    gluing to a global one.

    Parameters:
        (no constructor arguments; _violations defaults to empty list)

    Example::

        validator = ModuleStateValidator()
        ok = validator.validate_section(manifest)
        if not ok:
            for v in validator.report_violations():
                print(v)
    """

    _violations: list[str] = field(default_factory=list)

    def _add_violation(self, msg: str) -> None:
        """Append a violation string to the internal log."""
        self._violations.append(msg)
        logger.debug("Validation violation: %s", msg)

    def validate_section(self, manifest: ModuleStateManifest) -> bool:
        """Check that *manifest* represents a valid module section.

        Checks:

        * ``manifest.module_coordinate`` has at least one component.
        * ``manifest.global_names`` is non-empty.
        * Every name in ``global_names`` is a non-empty string.
        * Version is non-negative.

        Parameters:
            manifest: The :class:`ModuleStateManifest` to validate.

        Returns:
            ``True`` if all checks pass; ``False`` if any violation is found.
        """
        ok = True
        coord = manifest.module_coordinate
        if not coord.components:
            self._add_violation(
                f"Module coordinate for '{manifest.module_name}' has no "
                f"components"
            )
            ok = False
        if not manifest.global_names:
            self._add_violation(
                f"Module '{manifest.module_name}' has an empty global_names "
                f"tuple — the section is vacuous"
            )
            ok = False
        for name in manifest.global_names:
            if not name or not name.strip():
                self._add_violation(
                    f"Module '{manifest.module_name}' has an empty or "
                    f"whitespace-only name in global_names"
                )
                ok = False
                break
        if manifest.version < 0:
            self._add_violation(
                f"Module '{manifest.module_name}' has negative version: "
                f"{manifest.version}"
            )
            ok = False
        return ok

    def check_consistency(
        self,
        manifest1: ModuleStateManifest,
        manifest2: ModuleStateManifest,
    ) -> bool:
        """Check that shared names have the same type representation.

        Two manifests are *consistent* (in the sheaf sense) if, for every
        name present in both, they agree on the type representation.  A
        disagreement is a coherence failure: the two local sections cannot
        be glued into a single global section.

        Parameters:
            manifest1: First manifest (e.g. an earlier snapshot).
            manifest2: Second manifest (e.g. a later snapshot or view from a
                different call site).

        Returns:
            ``True`` if consistent; ``False`` if any conflicts are found.
        """
        ok = True
        names1 = set(manifest1.global_names)
        names2 = set(manifest2.global_names)
        shared = names1 & names2
        for name in sorted(shared):
            t1 = manifest1.type_reprs.get(name, "")
            t2 = manifest2.type_reprs.get(name, "")
            if t1 != t2:
                self._add_violation(
                    f"Consistency failure for name '{name}': "
                    f"manifest1 says '{t1}', manifest2 says '{t2}'"
                )
                ok = False
        return ok

    def check_monotonicity(
        self, snapshots: list[ModuleStateSnapshot]
    ) -> bool:
        """Verify that snapshot versions form a strictly increasing sequence.

        Parameters:
            snapshots: Ordered list of :class:`ModuleStateSnapshot` objects
                (as stored in :attr:`ModuleStateTracker._snapshots`).

        Returns:
            ``True`` if all version numbers are strictly increasing;
            ``False`` otherwise.
        """
        ok = True
        for i in range(1, len(snapshots)):
            prev = snapshots[i - 1].version
            curr = snapshots[i].version
            if curr <= prev:
                self._add_violation(
                    f"Non-monotonic snapshot sequence at index {i}: "
                    f"version {prev} followed by version {curr}"
                )
                ok = False
        return ok

    def report_violations(self) -> list[str]:
        """Return a copy of the accumulated violations list.

        Returns:
            List of human-readable violation strings, in the order they were
            detected.  The returned list is a copy; mutations do not affect
            the validator's internal state.
        """
        return list(self._violations)

    def validate_import_consistency(
        self, tracker: ImportTracker
    ) -> bool:
        """Check that no local name is imported from two different sources.

        In the sheaf model, importing the same local name from two sources
        creates an ambiguity in the section assignment: the section value at
        that name is under-determined.

        Parameters:
            tracker: The :class:`ImportTracker` to inspect.

        Returns:
            ``True`` if all local names are uniquely sourced; ``False`` if
            any name appears bound from two or more distinct source modules.
        """
        ok = True
        # Build reverse map: local_name -> list of source modules
        name_to_sources: dict[str, list[str]] = {}
        for event in tracker._imports:
            kind = event.get("kind")
            if kind == "star_import":
                # Star imports are handled separately — always flag
                self._add_violation(
                    f"Star import from '{event['module']}' makes namespace "
                    f"consistency undecidable"
                )
                ok = False
                continue
            local = event.get("local_name")
            src = event.get("module")
            if local and src:
                name_to_sources.setdefault(local, [])
                if src not in name_to_sources[local]:
                    name_to_sources[local].append(src)
        for name, sources in sorted(name_to_sources.items()):
            if len(sources) > 1:
                self._add_violation(
                    f"Name '{name}' imported from multiple sources: "
                    f"{sources} — section value is ambiguous"
                )
                ok = False
        return ok


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ModuleStateSnapshot",
    "ModuleStateTracker",
    "GlobalNameTracker",
    "ImportTracker",
    "ModuleStateValidator",
]
