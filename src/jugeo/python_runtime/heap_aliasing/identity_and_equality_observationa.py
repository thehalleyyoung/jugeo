"""Identity and equality: observational criteria — theory2.tex Ch17, §6.

This module implements the machinery for distinguishing *identity* (``is``) from
*observational equivalence* (``==``) in live Python programs, and for detecting
cases where the two criteria diverge or coincidentally agree (e.g. due to integer
interning or string interning in CPython).

In the sheaf-theoretic model of theory2.tex Ch17 §6:

* ``is`` checks *identity* — two references point to the same coordinate
  ``{id(obj)}``.  This is the strictest notion of equality: same section, same
  support.
* ``==`` checks *observational equivalence* — two sections have the same values
  over all observable fields.  Two objects can be observationally equivalent
  (``a == b``) but reside at different identity coordinates (``a is not b``).
* *Interning* is a CPython optimisation where distinct small integers and
  identifier-like strings are cached, causing ``is`` and ``==`` to coincidentally
  agree even though the objects are logically distinct.
* The *Frame Condition* states: if two sections agree on all observable fields,
  they are equivalent under ``==``.  Identity (``is``) is a strictly stronger
  condition.

The module provides static analysis (via :mod:`ast`) to find ``is``/``==``
comparisons in source code, and a runtime witness that records identity and
equality checks and flags divergences.

# copilot: s03 — identity vs equality observational criteria; classifies ``is``
#           and ``==`` comparisons, detects interning risks, and witnesses
#           runtime divergences between identity and observational equivalence.

Typical usage::

    coordinator = IdentityEqualityObservationalCriteriaCoordinator()
    result = coordinator.compare_identity(a, b)
    result = coordinator.compare_equality(a, b)
    report = coordinator.build_criteria_report([a, b, c])

    analyzer = IdentityEqualityObservationalCriteriaAnalyzer()
    report = analyzer.build_comparison_report(source_code)

    witness = IdentityEqualityObservationalCriteriaWitness()
    witness.witness_comparison(a, b, "is")
    evidence = witness.generate_observational_evidence()
"""

from __future__ import annotations

import ast
import inspect
import logging
import sys
import time
import types
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

try:
    from jugeo.geometry.site import CoordinateKind, CoordinateObject, MorphismKind, Site, SiteBuilder
    from jugeo.geometry.supports import SupportRegion, SupportSet, SupportTracker
    from jugeo.judgments.judgment_terms import (
        JudgmentStatus,
        TrustLevel,
    )
except ImportError:
    from enum import IntEnum

    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        """Stub for TrustLevel."""
        UNVERIFIED = 1
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

        def label(self) -> str:  # noqa: D102
            return self.name.lower()

    class CoordinateKind(str, Enum):  # type: ignore[no-redef]
        """Stub for CoordinateKind."""
        MODULE = "module"
        FUNCTION = "function"
        STATEMENT = "statement"
        EXPRESSION = "expression"

    class JudgmentStatus(str, Enum):  # type: ignore[no-redef]
        """Stub for JudgmentStatus."""
        PROPOSED = "proposed"
        SETTLED = "settled"
        OBSTRUCTED = "obstructed"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        """Stub for CoordinateObject."""
        coordinate_id: str = ""
        kind: str = "expression"
        label: str = ""

    @dataclass(frozen=True, slots=True)
    class SupportRegion:  # type: ignore[no-redef]
        """Stub for SupportRegion."""
        coordinate: str = ""

    class SupportSet:  # type: ignore[no-redef]
        """Stub for SupportSet."""
        def __init__(self, coordinates: frozenset[str] = frozenset()) -> None:
            self.coordinates = coordinates

    class SupportTracker:  # type: ignore[no-redef]
        """Stub for SupportTracker."""

    class Site:  # type: ignore[no-redef]
        """Stub for Site."""

    class SiteBuilder:  # type: ignore[no-redef]
        """Stub for SiteBuilder."""

    class MorphismKind(str, Enum):  # type: ignore[no-redef]
        """Stub for MorphismKind."""
        RESTRICTION = "restriction"
        EXTENSION = "extension"

try:
    from jugeo.python_runtime.heap_aliasing.models import (
        AliasEdge,
        AliasPartition,
        HeapObject,
        HeapSection,
        HeapSnapshot,
        IdentityCoordinate,
        MutationEvent,
        ObjectKind,
        make_heap_object,
        make_identity_coordinate,
    )
except ImportError:

    class ObjectKind(str, Enum):  # type: ignore[no-redef]
        """Stub for ObjectKind."""
        PRIMITIVE = "primitive"
        CONTAINER = "container"
        INSTANCE = "instance"
        FUNCTION = "function"
        MODULE = "module"
        FROZEN = "frozen"
        BUILTIN = "builtin"
        UNKNOWN = "unknown"

    @dataclass(frozen=True, slots=True)
    class IdentityCoordinate:  # type: ignore[no-redef]
        """Stub for IdentityCoordinate."""
        object_id: int = 0
        type_name: str = ""
        coordinate_key: str = ""
        creation_site: str = ""
        created_at: float = 0.0

    @dataclass(frozen=True, slots=True)
    class HeapObject:  # type: ignore[no-redef]
        """Stub for HeapObject."""
        object_id: int = 0
        type_name: str = ""
        kind: ObjectKind = ObjectKind.UNKNOWN
        field_keys: frozenset[str] = frozenset()
        creation_site: str = ""
        created_at: float = 0.0

        def is_container(self) -> bool:  # noqa: D102
            return self.kind == ObjectKind.CONTAINER

        def is_primitive(self) -> bool:  # noqa: D102
            return self.kind == ObjectKind.PRIMITIVE

    @dataclass(frozen=True, slots=True)
    class HeapSection:  # type: ignore[no-redef]
        """Stub for HeapSection."""
        section_id: str = ""
        coordinate_key: str = ""
        fields: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class AliasPartition:  # type: ignore[no-redef]
        """Stub for AliasPartition."""
        partition_id: str = ""
        member_keys: frozenset[str] = frozenset()

    @dataclass(frozen=True, slots=True)
    class MutationEvent:  # type: ignore[no-redef]
        """Stub for MutationEvent."""
        event_id: str = ""
        coordinate_key: str = ""
        field_name: str = ""
        old_value_repr: str = ""
        new_value_repr: str = ""
        timestamp: float = 0.0

    @dataclass(frozen=True, slots=True)
    class AliasEdge:  # type: ignore[no-redef]
        """Stub for AliasEdge."""
        source_key: str = ""
        target_key: str = ""
        kind: str = "alias"

    @dataclass(frozen=True, slots=True)
    class HeapSnapshot:  # type: ignore[no-redef]
        """Stub for HeapSnapshot."""
        snapshot_id: str = ""
        created_at: float = 0.0

    def make_identity_coordinate(obj: Any, creation_site: str = "") -> IdentityCoordinate:  # type: ignore[no-redef]
        """Stub factory for IdentityCoordinate."""
        return IdentityCoordinate(
            object_id=id(obj),
            type_name=type(obj).__name__,
            coordinate_key=f"id:{id(obj)}",
            creation_site=creation_site,
            created_at=time.time(),
        )

    def make_heap_object(obj: Any, creation_site: str = "") -> HeapObject:  # type: ignore[no-redef]
        """Stub factory for HeapObject."""
        kind_map = {list: ObjectKind.CONTAINER, dict: ObjectKind.CONTAINER,
                    set: ObjectKind.CONTAINER, tuple: ObjectKind.CONTAINER}
        tp = type(obj)
        kind = kind_map.get(tp, ObjectKind.INSTANCE)
        return HeapObject(
            object_id=id(obj),
            type_name=tp.__name__,
            kind=kind,
            field_keys=frozenset(),
            creation_site=creation_site,
            created_at=time.time(),
        )


_log = logging.getLogger(__name__)

_ANALYSIS_CHANNEL: str = "copilot-s03-identity-equality-observational"

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Section title used in generated reports.
SECTION_TITLE: str = "Identity and equality: observational criteria"

#: Version string for generated criteria reports.
CRITERIA_REPORT_VERSION: str = "1.0.0"

#: Channel name for evidence emission.
EVIDENCE_CHANNEL_NAME: str = "heap-aliasing.identity-equality"

#: CPython small-integer cache range lower bound.
SMALL_INT_MIN: int = -5

#: CPython small-integer cache range upper bound.
SMALL_INT_MAX: int = 256

#: Maximum length of strings considered interning candidates.
INTERNED_STRING_MAX_LEN: int = 20

#: Python primitive types (no heap identity).
PRIMITIVE_TYPES: tuple[type, ...] = (int, float, complex, bool, str, bytes, bytearray, type(None))

#: Comparison operator string for identity check.
OP_IS: str = "is"

#: Comparison operator string for equality check.
OP_EQ: str = "=="

#: Comparison operator string for inequality check.
OP_NE: str = "!="

#: Comparison operator string for identity-negative check.
OP_IS_NOT: str = "is not"

#: Correct uses of ``is`` — always valid.
SAFE_IS_COMPARANDS: frozenset[str] = frozenset({"None", "True", "False"})

#: Risk label for comparisons using ``is`` with potentially-interned values.
RISK_INTERNING_IS: str = "interning_risk_is"

#: Risk label for comparisons using ``is`` with container values.
RISK_CONTAINER_IS: str = "container_identity_ok"

#: Label for correct ``is None`` usage.
CORRECT_IS_NONE: str = "correct_is_none"

#: Label for correct ``is True``/``is False`` usage.
CORRECT_IS_BOOL: str = "correct_is_bool"

#: Maximum number of observer functions used in observational equivalence checks.
MAX_OBSERVERS: int = 64

#: Maximum number of comparison records to retain in a witness.
MAX_COMPARISON_RECORDS: int = 8192

#: Version of the witness evidence schema.
WITNESS_SCHEMA_VERSION: str = "1.0.0"

#: Sentinel label for unlabelled observations.
UNLABELLED: str = "<unlabelled>"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _new_uid() -> str:
    """Return a fresh unique identifier string.

    Returns:
        A hex UUID4 string prefixed with ``"uid_"``.
    """
    return f"uid_{uuid.uuid4().hex[:12]}"


def _safe_repr(obj: Any, max_len: int = 80) -> str:
    """Return a safe, length-limited repr of *obj*.

    Parameters:
        obj:     The object to represent.
        max_len: Maximum character length of the returned string.

    Returns:
        A string representation truncated to *max_len* characters.
    """
    try:
        r = repr(obj)
    except Exception:  # noqa: BLE001
        r = f"<repr-error:{type(obj).__name__}>"
    return r[:max_len - 1] + "…" if len(r) > max_len else r


def _is_small_integer(n: Any) -> bool:
    """Return ``True`` if *n* is in CPython's small-integer cache.

    CPython caches integers in ``[-5, 256]``.

    Parameters:
        n: The value to check.

    Returns:
        ``True`` if *n* is an int in ``[-5, 256]`` (excluding bool).
    """
    return isinstance(n, int) and not isinstance(n, bool) and SMALL_INT_MIN <= n <= SMALL_INT_MAX


def _is_interned_string(s: Any) -> bool:
    """Heuristically return ``True`` if *s* is likely a CPython interned string.

    CPython automatically interns identifier-like strings of moderate length.

    Parameters:
        s: The value to check.

    Returns:
        ``True`` if *s* is a str with ``len ≤ 20`` that looks like an identifier.
    """
    return isinstance(s, str) and len(s) <= INTERNED_STRING_MAX_LEN and s.isidentifier()


def _identity_key(obj: Any) -> str:
    """Return the canonical identity key for *obj*.

    Parameters:
        obj: Any Python object.

    Returns:
        ``"id:<id>"`` for heap objects, ``"val:<repr>"`` for primitives.
    """
    if isinstance(obj, PRIMITIVE_TYPES):
        return f"val:{_safe_repr(obj, 40)}"
    return f"id:{id(obj)}"


def _objects_are_observationally_equivalent(a: Any, b: Any, observers: list[Callable[[Any], Any]]) -> bool:
    """Check whether *a* and *b* are observationally equivalent under *observers*.

    Two objects are observationally equivalent if every observer function
    returns the same result for both.  This implements the Frame Condition
    from theory2.tex Ch17 §6.

    Parameters:
        a:         First object.
        b:         Second object.
        observers: List of callables, each taking a single object and
                   returning some observable value.

    Returns:
        ``True`` if all observers return equal values for *a* and *b*.
    """
    for obs in observers:
        try:
            va = obs(a)
            vb = obs(b)
            if va != vb:
                return False
        except Exception:  # noqa: BLE001
            return False
    return True


def _collect_observable_fields(obj: Any) -> dict[str, Any]:
    """Return a dictionary of observable (non-callable, public) fields of *obj*.

    Parameters:
        obj: Any Python object.

    Returns:
        A dict mapping field names to their current values.
    """
    fields: dict[str, Any] = {}
    try:
        if hasattr(obj, "__dict__"):
            for k, v in vars(obj).items():
                if not k.startswith("_") and not callable(v):
                    fields[k] = v
        else:
            for name, value in inspect.getmembers(obj, predicate=lambda v: not callable(v)):
                if not name.startswith("__"):
                    fields[name] = value
    except Exception:  # noqa: BLE001
        pass
    return fields


def _static_interning_risk(node: ast.expr) -> str | None:
    """Return a risk label if *node* may involve an interned value with ``is``.

    Parameters:
        node: An ``ast.expr`` node (the operand of an ``is`` comparison).

    Returns:
        A risk-label string if there is an interning risk, else ``None``.
    """
    if isinstance(node, ast.Constant):
        v = node.value
        if v is None:
            return CORRECT_IS_NONE
        if isinstance(v, bool):
            return CORRECT_IS_BOOL
        if _is_small_integer(v):
            return RISK_INTERNING_IS
        if _is_interned_string(v):
            return RISK_INTERNING_IS
    return None


# ---------------------------------------------------------------------------
# ComparisonKind enum
# ---------------------------------------------------------------------------


class ComparisonKind(str, Enum):
    """Discriminates between identity and equality comparisons.

    Members:
        IDENTITY:          ``is`` comparison — checks shared coordinate.
        IDENTITY_NEGATIVE: ``is not`` comparison.
        EQUALITY:          ``==`` comparison — checks observational equivalence.
        INEQUALITY:        ``!=`` comparison.
        UNKNOWN:           Comparison kind could not be determined.
    """

    IDENTITY = "identity"
    IDENTITY_NEGATIVE = "identity_negative"
    EQUALITY = "equality"
    INEQUALITY = "inequality"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# ComparisonRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComparisonRecord:
    """Immutable record of a single observed comparison.

    In sheaf terms, an identity comparison (``is``) checks whether two
    references share the same coordinate.  An equality comparison (``==``)
    checks whether their sections agree over observable fields.

    Attributes:
        record_id:         Unique record identifier.
        op:                The comparison operator (``"is"``, ``"=="``, etc.).
        kind:              :class:`ComparisonKind` classification.
        lhs_repr:          Repr of the left-hand side.
        rhs_repr:          Repr of the right-hand side.
        lhs_id:            ``id(lhs)`` (0 for primitives).
        rhs_id:            ``id(rhs)`` (0 for primitives).
        identity_result:   Result of ``lhs is rhs``.
        equality_result:   Result of ``lhs == rhs`` (``None`` on error).
        diverges:          ``True`` if ``identity_result != equality_result``.
        interning_risk:    ``True`` if the comparison is subject to interning.
        observed_at:       Unix timestamp of observation.
        provenance:        Tuple of provenance labels.
    """

    record_id: str
    op: str
    kind: ComparisonKind
    lhs_repr: str
    rhs_repr: str
    lhs_id: int
    rhs_id: int
    identity_result: bool
    equality_result: bool | None
    diverges: bool
    interning_risk: bool
    observed_at: float
    provenance: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain dictionary.

        Returns:
            A JSON-serialisable dict with all record fields.
        """
        return {
            "record_id": self.record_id,
            "op": self.op,
            "kind": self.kind.value,
            "lhs_repr": self.lhs_repr,
            "rhs_repr": self.rhs_repr,
            "lhs_id": self.lhs_id,
            "rhs_id": self.rhs_id,
            "identity_result": self.identity_result,
            "equality_result": self.equality_result,
            "diverges": self.diverges,
            "interning_risk": self.interning_risk,
            "observed_at": self.observed_at,
            "provenance": list(self.provenance),
        }

    def is_correct_is_none(self) -> bool:
        """Return ``True`` if this is a ``x is None`` comparison (always correct).

        Returns:
            ``True`` when one side is ``None`` and the operator is ``"is"``.
        """
        return self.op == OP_IS and ("None" in self.lhs_repr or "None" in self.rhs_repr)

    def is_divergence(self) -> bool:
        """Return ``True`` if identity and equality disagree.

        This is the key sheaf-theoretic violation: two objects at different
        coordinates that happen to be observationally equivalent (or vice versa).

        Returns:
            Value of :attr:`diverges`.
        """
        return self.diverges


# ---------------------------------------------------------------------------
# ObservationalEquivalenceRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObservationalEquivalenceRecord:
    """Records the result of an observational equivalence check.

    Two objects are observationally equivalent if they agree on all observable
    fields (Frame Condition).  This record captures the field-by-field comparison.

    Attributes:
        record_id:          Unique record identifier.
        lhs_repr:           Repr of the left-hand side.
        rhs_repr:           Repr of the right-hand side.
        lhs_id:             ``id(lhs)`` for the left object.
        rhs_id:             ``id(rhs)`` for the right object.
        same_identity:      Whether ``lhs is rhs``.
        same_equality:      Whether ``lhs == rhs``.
        field_agreements:   Dict mapping field name → whether both agree.
        observer_results:   List of ``(observer_index, lhs_val, rhs_val, agree)`` tuples.
        is_equiv:           Whether the objects are observationally equivalent.
        checked_at:         Unix timestamp of the check.
        provenance:         Tuple of provenance labels.
    """

    record_id: str
    lhs_repr: str
    rhs_repr: str
    lhs_id: int
    rhs_id: int
    same_identity: bool
    same_equality: bool
    field_agreements: dict[str, bool]
    observer_results: tuple[tuple[int, str, str, bool], ...]
    is_equiv: bool
    checked_at: float
    provenance: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain dictionary.

        Returns:
            A JSON-serialisable dict with all record fields.
        """
        return {
            "record_id": self.record_id,
            "lhs_repr": self.lhs_repr,
            "rhs_repr": self.rhs_repr,
            "lhs_id": self.lhs_id,
            "rhs_id": self.rhs_id,
            "same_identity": self.same_identity,
            "same_equality": self.same_equality,
            "field_agreements": self.field_agreements,
            "observer_results": [list(t) for t in self.observer_results],
            "is_equiv": self.is_equiv,
            "checked_at": self.checked_at,
            "provenance": list(self.provenance),
        }

    def diverges(self) -> bool:
        """Return ``True`` if identity and observational equivalence disagree.

        Returns:
            ``True`` when ``same_identity != is_equiv``.
        """
        return self.same_identity != self.is_equiv


# ---------------------------------------------------------------------------
# ComparisonASTVisitor
# ---------------------------------------------------------------------------


class ComparisonASTVisitor(ast.NodeVisitor):
    """AST visitor that collects ``is``/``==`` comparison nodes.

    Walks a Python AST and categorises each :class:`ast.Compare` node as an
    identity check (``is``, ``is not``) or an equality check (``==``, ``!=``).
    Flags potentially risky uses of ``is`` with interned values.

    Attributes:
        is_comparisons:    List of dicts for ``is``/``is not`` comparison nodes.
        eq_comparisons:    List of dicts for ``==``/``!=`` comparison nodes.
        interning_risks:   List of dicts for risky ``is`` comparisons.
        correct_is_usages: List of dicts for correct ``is None``/``is True``/``is False``.
        other_comparisons: List of dicts for other comparison ops (``<``, ``>``, etc.).
    """

    def __init__(self) -> None:
        """Initialise with empty accumulators."""
        self.is_comparisons: list[dict[str, Any]] = []
        self.eq_comparisons: list[dict[str, Any]] = []
        self.interning_risks: list[dict[str, Any]] = []
        self.correct_is_usages: list[dict[str, Any]] = []
        self.other_comparisons: list[dict[str, Any]] = []

    def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
        """Process a comparison node.

        Parameters:
            node: The ``ast.Compare`` node to visit.
        """
        for op, comparator in zip(node.ops, node.comparators):
            entry: dict[str, Any] = {
                "lineno": getattr(node, "lineno", None),
                "col_offset": getattr(node, "col_offset", None),
                "lhs_type": type(node.left).__name__,
                "op_type": type(op).__name__,
                "rhs_type": type(comparator).__name__,
            }
            if isinstance(op, ast.Is):
                entry["op"] = OP_IS
                risk = _static_interning_risk(comparator) or _static_interning_risk(node.left)
                if risk in (CORRECT_IS_NONE, CORRECT_IS_BOOL):
                    entry["risk"] = risk
                    self.correct_is_usages.append(entry)
                elif risk == RISK_INTERNING_IS:
                    entry["risk"] = RISK_INTERNING_IS
                    self.interning_risks.append(entry)
                else:
                    entry["risk"] = None
                self.is_comparisons.append(entry)
            elif isinstance(op, ast.IsNot):
                entry["op"] = OP_IS_NOT
                entry["risk"] = _static_interning_risk(comparator) or _static_interning_risk(node.left)
                self.is_comparisons.append(entry)
            elif isinstance(op, ast.Eq):
                entry["op"] = OP_EQ
                self.eq_comparisons.append(entry)
            elif isinstance(op, ast.NotEq):
                entry["op"] = OP_NE
                self.eq_comparisons.append(entry)
            else:
                entry["op"] = type(op).__name__
                self.other_comparisons.append(entry)
        self.generic_visit(node)

    def summary(self) -> dict[str, Any]:
        """Return a summary of accumulated comparison observations.

        Returns:
            A dictionary with counts of identity, equality, interning-risk,
            correct-is, and other comparisons.
        """
        return {
            "is_comparison_count": len(self.is_comparisons),
            "eq_comparison_count": len(self.eq_comparisons),
            "interning_risk_count": len(self.interning_risks),
            "correct_is_usage_count": len(self.correct_is_usages),
            "other_comparison_count": len(self.other_comparisons),
            "interning_risks": self.interning_risks[:10],
            "correct_is_usages": self.correct_is_usages[:10],
        }


# ---------------------------------------------------------------------------
# IdentityEqualityObservationalCriteriaCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IdentityEqualityObservationalCriteriaCoordinator:
    """Coordinates identity vs equality analysis over Python objects.

    Provides methods for comparing objects by identity (``is``) and by
    observational equivalence (``==``), computing equivalence classes,
    and analysing source code for comparison patterns.

    In the sheaf model:
    - :meth:`compare_identity` checks ``a is b`` (same coordinate).
    - :meth:`compare_equality` checks ``a == b`` (same section values).
    - :meth:`check_observational_equivalence` checks the Frame Condition
      using a list of observer functions.

    Attributes:
        _comparison_records:   All :class:`ComparisonRecord` instances observed.
        _equiv_records:        All :class:`ObservationalEquivalenceRecord` instances.
        _criteria_log:         Log of (timestamp, message) tuples.
        _divergence_count:     Running count of identity/equality divergences.
        coordinator_id:        Unique coordinator identifier.
        created_at:            Creation timestamp.

    Examples:
        >>> coord = IdentityEqualityObservationalCriteriaCoordinator()
        >>> coord.compare_identity(42, 42)
        True
        >>> coord.compare_equality([1], [1])
        True
    """

    _comparison_records: list[ComparisonRecord] = field(default_factory=list)
    _equiv_records: list[ObservationalEquivalenceRecord] = field(default_factory=list)
    _criteria_log: list[tuple[float, str]] = field(default_factory=list)
    _divergence_count: int = field(default=0)
    coordinator_id: str = field(default_factory=_new_uid)
    created_at: float = field(default_factory=time.time)

    def compare_identity(self, a: Any, b: Any) -> bool:
        """Return ``True`` if *a* and *b* are identical (``a is b``).

        Records the comparison in the internal log.

        Parameters:
            a: First object.
            b: Second object.

        Returns:
            ``True`` if ``a is b``.
        """
        result = a is b
        self._criteria_log.append((time.time(), f"compare_identity({type(a).__name__}, {type(b).__name__}) → {result}"))
        return result

    def compare_equality(self, a: Any, b: Any) -> bool | None:
        """Return the result of ``a == b``, or ``None`` on error.

        Records the comparison in the internal log.

        Parameters:
            a: First object.
            b: Second object.

        Returns:
            The result of ``a == b``, or ``None`` if an exception occurs.
        """
        try:
            result: bool | None = a == b
        except Exception:  # noqa: BLE001
            result = None
        self._criteria_log.append((time.time(), f"compare_equality({type(a).__name__}, {type(b).__name__}) → {result}"))
        return result

    def analyze_equivalence_class(self, objects: list[Any]) -> dict[str, Any]:
        """Compute identity and equality equivalence classes for *objects*.

        Groups objects by identity (``is``) to form identity classes, and by
        equality (``==``) to form equality classes.

        Parameters:
            objects: A list of Python objects.

        Returns:
            A dictionary with:
            - ``"identity_classes"``: list of lists of object indices sharing
              the same identity (``is``).
            - ``"equality_classes"``: list of lists of object indices that are
              equal (``==``).
            - ``"divergences"``: list of ``(i, j)`` pairs where identity and
              equality disagree.
            - ``"object_count"``: total number of objects.
        """
        n = len(objects)
        identity_groups: dict[int, list[int]] = {}  # id(obj) → indices
        eq_checked: dict[tuple[int, int], bool] = {}

        for i, obj in enumerate(objects):
            key = id(obj)
            identity_groups.setdefault(key, []).append(i)

        identity_classes = [sorted(indices) for indices in identity_groups.values() if len(indices) > 1]

        # Build equality classes using union-find over indices
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union_eq(x: int, y: int) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for i in range(n):
            for j in range(i + 1, n):
                try:
                    if objects[i] == objects[j]:
                        union_eq(i, j)
                        eq_checked[(i, j)] = True
                    else:
                        eq_checked[(i, j)] = False
                except Exception:  # noqa: BLE001
                    eq_checked[(i, j)] = False

        eq_groups: dict[int, list[int]] = {}
        for i in range(n):
            root = find(i)
            eq_groups.setdefault(root, []).append(i)
        equality_classes = [sorted(indices) for indices in eq_groups.values() if len(indices) > 1]

        # Find divergences: (i, j) where (i is j) != (i == j)
        divergences: list[tuple[int, int]] = []
        for (i, j), eq_result in eq_checked.items():
            id_result = objects[i] is objects[j]
            if id_result != eq_result:
                divergences.append((i, j))
                self._divergence_count += 1

        self._criteria_log.append((time.time(), f"analyze_equivalence_class: {n} objects, {len(divergences)} divergences"))
        return {
            "identity_classes": identity_classes,
            "equality_classes": equality_classes,
            "divergences": divergences,
            "object_count": n,
        }

    def check_observational_equivalence(
        self,
        a: Any,
        b: Any,
        observers: list[Callable[[Any], Any]],
    ) -> ObservationalEquivalenceRecord:
        """Check whether *a* and *b* are observationally equivalent.

        Applies each observer function to both *a* and *b*, recording whether
        their outputs agree.  Also checks ``a is b`` and ``a == b`` for context.

        This implements the Frame Condition from theory2.tex Ch17 §6:
        two objects are equivalent iff they agree on all observable fields.

        Parameters:
            a:         First object.
            b:         Second object.
            observers: List of observer functions.  Each is called with a
                       single object and should return a comparable value.

        Returns:
            An :class:`ObservationalEquivalenceRecord` capturing the full
            comparison.
        """
        same_id = a is b
        try:
            same_eq: bool | None = a == b
        except Exception:  # noqa: BLE001
            same_eq = None

        observer_results: list[tuple[int, str, str, bool]] = []
        all_agree = True
        for i, obs in enumerate(observers[:MAX_OBSERVERS]):
            try:
                va = obs(a)
                vb = obs(b)
                agree = va == vb
            except Exception:  # noqa: BLE001
                va, vb, agree = None, None, False
            observer_results.append((i, _safe_repr(va), _safe_repr(vb), agree))
            if not agree:
                all_agree = False

        # Also check field-level agreement
        fa = _collect_observable_fields(a)
        fb = _collect_observable_fields(b)
        field_agreements: dict[str, bool] = {}
        all_fields = set(fa.keys()) | set(fb.keys())
        for fname in all_fields:
            try:
                field_agreements[fname] = fa.get(fname) == fb.get(fname)
            except Exception:  # noqa: BLE001
                field_agreements[fname] = False

        if field_agreements:
            all_agree = all_agree and all(field_agreements.values())

        rec = ObservationalEquivalenceRecord(
            record_id=_new_uid(),
            lhs_repr=_safe_repr(a),
            rhs_repr=_safe_repr(b),
            lhs_id=id(a) if not isinstance(a, PRIMITIVE_TYPES) else 0,
            rhs_id=id(b) if not isinstance(b, PRIMITIVE_TYPES) else 0,
            same_identity=same_id,
            same_equality=bool(same_eq) if same_eq is not None else False,
            field_agreements=field_agreements,
            observer_results=tuple(observer_results),
            is_equiv=all_agree,
            checked_at=time.time(),
            provenance=(self.coordinator_id, "check_observational_equivalence"),
        )
        self._equiv_records.append(rec)
        if rec.diverges():
            self._divergence_count += 1
        self._criteria_log.append((time.time(), f"obs_equiv: {type(a).__name__} vs {type(b).__name__} → {all_agree}"))
        return rec

    def build_criteria_report(self, objects: list[Any]) -> dict[str, Any]:
        """Build a comprehensive criteria report for *objects*.

        Performs :meth:`analyze_equivalence_class` and records statistics.

        Parameters:
            objects: A list of Python objects.

        Returns:
            A dictionary with:
            - ``"version"``: report version.
            - ``"report_id"``: unique report ID.
            - ``"coordinator_id"``: this coordinator's ID.
            - ``"section"``: section title.
            - ``"object_count"``: total objects.
            - ``"identity_class_count"``: number of identity equivalence classes.
            - ``"equality_class_count"``: number of equality equivalence classes.
            - ``"divergence_count"``: total divergences found.
            - ``"comparison_record_count"``: total comparison records.
            - ``"equiv_record_count"``: total observational equivalence records.
            - ``"generated_at"``: Unix timestamp.
        """
        eq_analysis = self.analyze_equivalence_class(objects)
        return {
            "version": CRITERIA_REPORT_VERSION,
            "report_id": _new_uid(),
            "coordinator_id": self.coordinator_id,
            "section": SECTION_TITLE,
            "object_count": len(objects),
            "identity_class_count": len(eq_analysis["identity_classes"]),
            "equality_class_count": len(eq_analysis["equality_classes"]),
            "divergence_count": len(eq_analysis["divergences"]),
            "cumulative_divergence_count": self._divergence_count,
            "comparison_record_count": len(self._comparison_records),
            "equiv_record_count": len(self._equiv_records),
            "criteria_log_entries": len(self._criteria_log),
            "generated_at": time.time(),
        }

    def find_identity_vs_equality_violations(self, source: str) -> list[dict[str, Any]]:
        """Find potential identity/equality violations in Python source.

        Uses the :class:`ComparisonASTVisitor` to locate risky ``is``
        comparisons and report them as potential sheaf-condition violations.

        Parameters:
            source: Python source code string.

        Returns:
            A list of dicts describing each potential violation, with
            ``"lineno"``, ``"op"``, ``"risk"``, and ``"description"`` fields.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        visitor = ComparisonASTVisitor()
        visitor.visit(tree)
        violations: list[dict[str, Any]] = []
        for entry in visitor.interning_risks:
            violations.append({**entry, "description": "is comparison with potentially-interned literal"})
        self._criteria_log.append((time.time(), f"find_violations: {len(violations)} in {len(source)} chars"))
        return violations

    def reset(self) -> None:
        """Reset all accumulated state.

        Preserves ``coordinator_id`` and ``created_at``.
        """
        self._comparison_records.clear()
        self._equiv_records.clear()
        self._criteria_log.clear()
        self._divergence_count = 0
        _log.debug("IdentityEqualityObservationalCriteriaCoordinator %s: reset", self.coordinator_id)


# ---------------------------------------------------------------------------
# IdentityEqualityObservationalCriteriaAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IdentityEqualityObservationalCriteriaAnalyzer:
    """Statically analyses Python source for ``is``/``==`` comparison patterns.

    Uses :mod:`ast` to parse Python source code and classify each comparison
    node as an identity check or an equality check.  Detects interning risks
    and correct ``is None``/``is True``/``is False`` patterns.

    Attributes:
        _parse_cache:   Memoisation cache from source hash → AST.
        _report_cache:  Memoisation cache from source hash → report dict.
        _visitor_log:   Log of (timestamp, node_count) tuples.
        analyzer_id:    Unique analyser identifier.
        created_at:     Creation timestamp.

    Examples:
        >>> analyzer = IdentityEqualityObservationalCriteriaAnalyzer()
        >>> report = analyzer.build_comparison_report("if x is None: pass")
        >>> report["is_comparison_count"]
        1
    """

    _parse_cache: dict[str, ast.Module] = field(default_factory=dict)
    _report_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    _visitor_log: list[tuple[float, int]] = field(default_factory=list)
    analyzer_id: str = field(default_factory=_new_uid)
    created_at: float = field(default_factory=time.time)

    def _hash_source(self, source: str) -> str:
        """Return a short hash of *source* for caching.

        Parameters:
            source: Python source code.

        Returns:
            A 16-char hex digest string.
        """
        import hashlib
        return hashlib.md5(source.encode(), usedforsecurity=False).hexdigest()[:16]

    def _parse_source(self, source: str) -> ast.Module | None:
        """Parse *source*, using the cache when available.

        Parameters:
            source: Python source code string.

        Returns:
            The parsed :class:`ast.Module`, or ``None`` on error.
        """
        h = self._hash_source(source)
        if h in self._parse_cache:
            return self._parse_cache[h]
        try:
            tree = ast.parse(source)
            self._parse_cache[h] = tree
            return tree
        except SyntaxError:
            return None

    def analyze_comparisons(self, source: str) -> dict[str, Any]:
        """Analyse all comparison nodes in *source*.

        Parameters:
            source: Python source code string.

        Returns:
            A summary dict from :meth:`ComparisonASTVisitor.summary` plus
            ``"parse_ok"`` and ``"analyzer_id"`` fields.
        """
        tree = self._parse_source(source)
        if tree is None:
            return {"parse_ok": False, "analyzer_id": self.analyzer_id}
        visitor = ComparisonASTVisitor()
        visitor.visit(tree)
        self._visitor_log.append((time.time(), sum(1 for _ in ast.walk(tree))))
        summary = visitor.summary()
        summary["parse_ok"] = True
        summary["analyzer_id"] = self.analyzer_id
        return summary

    def find_is_comparisons(self, tree: ast.Module) -> list[dict[str, Any]]:
        """Find all ``is``/``is not`` comparison nodes in *tree*.

        Parameters:
            tree: A parsed :class:`ast.Module`.

        Returns:
            A list of comparison detail dicts for ``is``/``is not`` operations.
        """
        visitor = ComparisonASTVisitor()
        visitor.visit(tree)
        return visitor.is_comparisons

    def find_eq_comparisons(self, tree: ast.Module) -> list[dict[str, Any]]:
        """Find all ``==``/``!=`` comparison nodes in *tree*.

        Parameters:
            tree: A parsed :class:`ast.Module`.

        Returns:
            A list of comparison detail dicts for ``==``/``!=`` operations.
        """
        visitor = ComparisonASTVisitor()
        visitor.visit(tree)
        return visitor.eq_comparisons

    def classify_comparison(self, node: ast.Compare) -> ComparisonKind:
        """Classify the primary operator of an :class:`ast.Compare` node.

        Parameters:
            node: An ``ast.Compare`` node.

        Returns:
            The :class:`ComparisonKind` of the first comparison operator.
        """
        if not node.ops:
            return ComparisonKind.UNKNOWN
        op = node.ops[0]
        if isinstance(op, ast.Is):
            return ComparisonKind.IDENTITY
        if isinstance(op, ast.IsNot):
            return ComparisonKind.IDENTITY_NEGATIVE
        if isinstance(op, ast.Eq):
            return ComparisonKind.EQUALITY
        if isinstance(op, ast.NotEq):
            return ComparisonKind.INEQUALITY
        return ComparisonKind.UNKNOWN

    def check_interning_risks(self, source: str) -> list[dict[str, Any]]:
        """Return all interning-risk comparison nodes found in *source*.

        Parameters:
            source: Python source code string.

        Returns:
            A list of dicts for each risky ``is`` comparison found (comparisons
            with small integers or identifier-like string literals).
        """
        tree = self._parse_source(source)
        if tree is None:
            return []
        visitor = ComparisonASTVisitor()
        visitor.visit(tree)
        return visitor.interning_risks

    def build_comparison_report(self, source: str) -> dict[str, Any]:
        """Build a comprehensive comparison analysis report for *source*.

        Parses *source*, classifies all comparison nodes, and produces a
        report with statistics and classified comparisons.

        Parameters:
            source: Python source code string.

        Returns:
            A dictionary with:
            - ``"analyzer_id"``: this analyser's ID.
            - ``"parse_ok"``: whether parsing succeeded.
            - ``"parse_error"``: error message or ``None``.
            - ``"is_comparison_count"``: count of identity comparisons.
            - ``"eq_comparison_count"``: count of equality comparisons.
            - ``"interning_risk_count"``: count of risky ``is`` comparisons.
            - ``"correct_is_usage_count"``: count of correct ``is None``/bool.
            - ``"other_comparison_count"``: count of other comparisons.
            - ``"is_comparisons"``: list of identity comparison dicts.
            - ``"eq_comparisons"``: list of equality comparison dicts.
            - ``"interning_risks"``: list of interning-risk dicts.
            - ``"correct_is_usages"``: list of correct ``is`` usage dicts.
            - ``"node_total"``: total AST nodes.
            - ``"generated_at"``: Unix timestamp.
        """
        h = self._hash_source(source)
        if h in self._report_cache:
            return self._report_cache[h]

        report: dict[str, Any] = {
            "analyzer_id": self.analyzer_id,
            "parse_ok": False,
            "parse_error": None,
            "is_comparison_count": 0,
            "eq_comparison_count": 0,
            "interning_risk_count": 0,
            "correct_is_usage_count": 0,
            "other_comparison_count": 0,
            "is_comparisons": [],
            "eq_comparisons": [],
            "interning_risks": [],
            "correct_is_usages": [],
            "node_total": 0,
            "generated_at": time.time(),
        }
        tree = self._parse_source(source)
        if tree is None:
            report["parse_error"] = "SyntaxError"
            return report

        report["parse_ok"] = True
        all_nodes = list(ast.walk(tree))
        report["node_total"] = len(all_nodes)
        visitor = ComparisonASTVisitor()
        visitor.visit(tree)

        summary = visitor.summary()
        report.update(summary)
        report["is_comparisons"] = visitor.is_comparisons
        report["eq_comparisons"] = visitor.eq_comparisons
        report["interning_risks"] = visitor.interning_risks
        report["correct_is_usages"] = visitor.correct_is_usages

        self._report_cache[h] = report
        self._visitor_log.append((time.time(), len(all_nodes)))
        return report

    def analyze_function_comparisons(self, func: types.FunctionType) -> dict[str, Any]:
        """Analyse comparison patterns in the source of *func*.

        Retrieves the source of *func* using :func:`inspect.getsource` and
        runs :meth:`build_comparison_report` on it.

        Parameters:
            func: A Python function whose source is accessible.

        Returns:
            The report from :meth:`build_comparison_report`, or an error dict.
        """
        try:
            source = inspect.getsource(func)
        except (OSError, TypeError) as exc:
            return {"error": str(exc), "analyzer_id": self.analyzer_id}
        return self.build_comparison_report(source)

    def get_cache_stats(self) -> dict[str, Any]:
        """Return statistics about the analyser's internal caches.

        Returns:
            A dict with ``"parse_cache_size"``, ``"report_cache_size"``,
            ``"log_entries"``, and ``"analyzer_id"``.
        """
        return {
            "parse_cache_size": len(self._parse_cache),
            "report_cache_size": len(self._report_cache),
            "log_entries": len(self._visitor_log),
            "analyzer_id": self.analyzer_id,
        }


# ---------------------------------------------------------------------------
# IdentityEqualityObservationalCriteriaWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IdentityEqualityObservationalCriteriaWitness:
    """Runtime witness for identity vs equality observations.

    Observes live comparisons between Python objects at runtime, records
    :class:`ComparisonRecord` instances, and identifies cases where ``is``
    and ``==`` disagree (divergences).

    In the sheaf model, a divergence is a pair of objects at different
    identity coordinates that are nonetheless observationally equivalent —
    or, conversely, objects at the same coordinate that fail ``==`` (which
    should never happen for well-behaved types).

    Attributes:
        _comparison_records:   All witnessed :class:`ComparisonRecord` instances.
        _divergence_records:   Records where ``is`` and ``==`` disagree.
        _observation_log:      Log of ``(timestamp, op, result)`` tuples.
        _identity_checks:      Sublist of identity-check records.
        _equality_checks:      Sublist of equality-check records.
        witness_id:            Unique witness identifier.
        created_at:            Creation timestamp.

    Examples:
        >>> witness = IdentityEqualityObservationalCriteriaWitness()
        >>> witness.witness_comparison(1, 1, "is")
        >>> witness.witness_comparison([1], [1], "==")
        >>> summary = witness.get_criteria_summary()
        >>> summary["total_comparisons"]
        2
    """

    _comparison_records: list[ComparisonRecord] = field(default_factory=list)
    _divergence_records: list[ComparisonRecord] = field(default_factory=list)
    _observation_log: list[tuple[float, str, bool | None]] = field(default_factory=list)
    _identity_checks: list[ComparisonRecord] = field(default_factory=list)
    _equality_checks: list[ComparisonRecord] = field(default_factory=list)
    witness_id: str = field(default_factory=_new_uid)
    created_at: float = field(default_factory=time.time)

    def witness_comparison(self, a: Any, b: Any, op: str = OP_EQ) -> ComparisonRecord:
        """Witness a comparison between *a* and *b* with operator *op*.

        Computes both ``a is b`` and ``a == b``, determines whether they
        diverge, and records a :class:`ComparisonRecord`.

        Parameters:
            a:  Left-hand operand.
            b:  Right-hand operand.
            op: The comparison operator string (e.g. ``"is"``, ``"=="``,
                ``"is not"``, ``"!="``).

        Returns:
            The :class:`ComparisonRecord` created for this observation.
        """
        id_result = a is b
        try:
            eq_result: bool | None = a == b
        except Exception:  # noqa: BLE001
            eq_result = None

        if op in (OP_IS, OP_IS_NOT):
            kind = ComparisonKind.IDENTITY if op == OP_IS else ComparisonKind.IDENTITY_NEGATIVE
        elif op in (OP_EQ, OP_NE):
            kind = ComparisonKind.EQUALITY if op == OP_EQ else ComparisonKind.INEQUALITY
        else:
            kind = ComparisonKind.UNKNOWN

        interning_risk = (
            (_is_small_integer(a) or _is_small_integer(b) or _is_interned_string(a) or _is_interned_string(b))
            if isinstance(a, (int, str)) or isinstance(b, (int, str)) else False
        )
        diverges = eq_result is not None and (id_result != eq_result)

        rec = ComparisonRecord(
            record_id=_new_uid(),
            op=op,
            kind=kind,
            lhs_repr=_safe_repr(a),
            rhs_repr=_safe_repr(b),
            lhs_id=id(a) if not isinstance(a, PRIMITIVE_TYPES) else 0,
            rhs_id=id(b) if not isinstance(b, PRIMITIVE_TYPES) else 0,
            identity_result=id_result,
            equality_result=eq_result,
            diverges=diverges,
            interning_risk=interning_risk,
            observed_at=time.time(),
            provenance=(self.witness_id, op),
        )
        self._comparison_records.append(rec)
        if kind in (ComparisonKind.IDENTITY, ComparisonKind.IDENTITY_NEGATIVE):
            self._identity_checks.append(rec)
        elif kind in (ComparisonKind.EQUALITY, ComparisonKind.INEQUALITY):
            self._equality_checks.append(rec)
        if diverges:
            self._divergence_records.append(rec)
        self._observation_log.append((time.time(), op, id_result))
        return rec

    def record_identity_check(self, a: Any, b: Any, result: bool) -> ComparisonRecord:
        """Record an explicit identity check result.

        Use this when the ``is`` result is already known (e.g. from a
        profiler or bytecode inspector) and does not need to be recomputed.

        Parameters:
            a:      Left-hand operand.
            b:      Right-hand operand.
            result: The known result of ``a is b``.

        Returns:
            The :class:`ComparisonRecord` created.
        """
        try:
            eq_result: bool | None = a == b
        except Exception:  # noqa: BLE001
            eq_result = None
        diverges = eq_result is not None and (result != eq_result)
        rec = ComparisonRecord(
            record_id=_new_uid(),
            op=OP_IS,
            kind=ComparisonKind.IDENTITY,
            lhs_repr=_safe_repr(a),
            rhs_repr=_safe_repr(b),
            lhs_id=id(a) if not isinstance(a, PRIMITIVE_TYPES) else 0,
            rhs_id=id(b) if not isinstance(b, PRIMITIVE_TYPES) else 0,
            identity_result=result,
            equality_result=eq_result,
            diverges=diverges,
            interning_risk=_is_small_integer(a) or _is_small_integer(b) or _is_interned_string(a) or _is_interned_string(b),
            observed_at=time.time(),
            provenance=(self.witness_id, "record_identity_check"),
        )
        self._comparison_records.append(rec)
        self._identity_checks.append(rec)
        if diverges:
            self._divergence_records.append(rec)
        self._observation_log.append((time.time(), OP_IS, result))
        return rec

    def record_equality_check(self, a: Any, b: Any, result: bool) -> ComparisonRecord:
        """Record an explicit equality check result.

        Use this when the ``==`` result is already known.

        Parameters:
            a:      Left-hand operand.
            b:      Right-hand operand.
            result: The known result of ``a == b``.

        Returns:
            The :class:`ComparisonRecord` created.
        """
        id_result = a is b
        diverges = id_result != result
        rec = ComparisonRecord(
            record_id=_new_uid(),
            op=OP_EQ,
            kind=ComparisonKind.EQUALITY,
            lhs_repr=_safe_repr(a),
            rhs_repr=_safe_repr(b),
            lhs_id=id(a) if not isinstance(a, PRIMITIVE_TYPES) else 0,
            rhs_id=id(b) if not isinstance(b, PRIMITIVE_TYPES) else 0,
            identity_result=id_result,
            equality_result=result,
            diverges=diverges,
            interning_risk=False,
            observed_at=time.time(),
            provenance=(self.witness_id, "record_equality_check"),
        )
        self._comparison_records.append(rec)
        self._equality_checks.append(rec)
        if diverges:
            self._divergence_records.append(rec)
        self._observation_log.append((time.time(), OP_EQ, result))
        return rec

    def find_identity_equality_divergence(self, objects: list[Any]) -> list[tuple[Any, Any, bool, bool | None]]:
        """Find all pairs in *objects* where ``is`` and ``==`` disagree.

        Parameters:
            objects: A list of Python objects.

        Returns:
            A list of ``(a, b, id_result, eq_result)`` tuples for each
            diverging pair found.
        """
        divergences: list[tuple[Any, Any, bool, bool | None]] = []
        n = len(objects)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = objects[i], objects[j]
                id_r = a is b
                try:
                    eq_r: bool | None = a == b
                except Exception:  # noqa: BLE001
                    eq_r = None
                if eq_r is not None and id_r != eq_r:
                    divergences.append((a, b, id_r, eq_r))
        self._observation_log.append((time.time(), "find_divergence", bool(divergences)))
        return divergences

    def generate_observational_evidence(self) -> dict[str, Any]:
        """Generate an evidence bundle from all accumulated observations.

        Returns:
            A dictionary modelling a sheaf-theoretic evidence bundle:
            - ``"bundle_id"``: unique identifier.
            - ``"witness_id"``: this witness's ID.
            - ``"channel"``: evidence channel name.
            - ``"section"``: section title.
            - ``"comparison_records"``: list of serialised comparison records.
            - ``"divergence_records"``: list of serialised divergence records.
            - ``"statistics"``: summary statistics.
            - ``"generated_at"``: Unix timestamp.
        """
        return {
            "bundle_id": _new_uid(),
            "witness_id": self.witness_id,
            "channel": EVIDENCE_CHANNEL_NAME,
            "section": SECTION_TITLE,
            "schema_version": WITNESS_SCHEMA_VERSION,
            "comparison_records": [r.to_dict() for r in self._comparison_records],
            "divergence_records": [r.to_dict() for r in self._divergence_records],
            "statistics": self.get_criteria_summary(),
            "generated_at": time.time(),
        }

    def get_criteria_summary(self) -> dict[str, Any]:
        """Return a concise summary of all witnessed comparisons.

        Returns:
            A dictionary with ``"total_comparisons"``, ``"identity_check_count"``,
            ``"equality_check_count"``, ``"divergence_count"``,
            ``"interning_risk_count"``, and ``"witness_id"``.
        """
        return {
            "witness_id": self.witness_id,
            "total_comparisons": len(self._comparison_records),
            "identity_check_count": len(self._identity_checks),
            "equality_check_count": len(self._equality_checks),
            "divergence_count": len(self._divergence_records),
            "interning_risk_count": sum(1 for r in self._comparison_records if r.interning_risk),
            "observation_log_entries": len(self._observation_log),
        }

    def reset(self) -> None:
        """Clear all accumulated witness records and logs.

        Preserves ``witness_id`` and ``created_at``.
        """
        self._comparison_records.clear()
        self._divergence_records.clear()
        self._observation_log.clear()
        self._identity_checks.clear()
        self._equality_checks.clear()
        _log.debug("IdentityEqualityObservationalCriteriaWitness %s: reset", self.witness_id)

    def get_observation_log(self) -> list[tuple[float, str, bool | None]]:
        """Return a copy of the raw observation log.

        Returns:
            A list of ``(timestamp, op, result)`` tuples.
        """
        return list(self._observation_log)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def _smoke_test() -> None:
    """Quick sanity check for the identity/equality observational machinery.

    Exercises :class:`IdentityEqualityObservationalCriteriaCoordinator`,
    :class:`IdentityEqualityObservationalCriteriaAnalyzer`, and
    :class:`IdentityEqualityObservationalCriteriaWitness` with a range of
    Python objects including interned values, containers, and user instances.
    Raises :class:`AssertionError` on failure.
    """
    print(f"[{_ANALYSIS_CHANNEL}] smoke test starting …")

    # --- Helpers ---
    assert _is_small_integer(0)
    assert _is_small_integer(256)
    assert not _is_small_integer(257)
    assert not _is_small_integer(True)  # bool excluded
    assert _is_interned_string("hello")
    assert _is_interned_string("my_var")
    assert not _is_interned_string("not an identifier!")
    assert not _is_interned_string("a" * 21)

    # --- Coordinator ---
    coord = IdentityEqualityObservationalCriteriaCoordinator()

    # identity
    a = [1, 2, 3]
    b = a
    c = [1, 2, 3]
    assert coord.compare_identity(a, b), "a is b should be True"
    assert not coord.compare_identity(a, c), "a is c should be False"

    # equality
    assert coord.compare_equality(a, c), "a == c should be True"
    assert not coord.compare_equality(a, [1, 2, 4])

    # equivalence class
    eq_result = coord.analyze_equivalence_class([a, b, c, [1, 2, 3], [9]])
    assert len(eq_result["identity_classes"]) == 1, f"expected 1 identity class, got {eq_result['identity_classes']}"
    assert len(eq_result["equality_classes"]) >= 1
    assert len(eq_result["divergences"]) >= 1  # a is not c, but a == c

    # observational equivalence with observers
    observers = [len, lambda x: x[0] if x else None]
    equiv_rec = coord.check_observational_equivalence(a, c, observers)
    assert equiv_rec.is_equiv, "a and c should be obs-equivalent under len and x[0]"
    assert equiv_rec.diverges(), "identity differs but obs-equiv"

    # criteria report
    report = coord.build_criteria_report([a, b, c])
    assert report["identity_class_count"] >= 1
    assert report["divergence_count"] >= 0

    # violations from source
    src_with_risk = "if x is 5: pass\nif y is 'hello': pass\nif z is None: pass"
    violations = coord.find_identity_vs_equality_violations(src_with_risk)
    assert len(violations) >= 1, f"expected ≥1 violation, got {violations}"

    # --- Analyzer ---
    analyzer = IdentityEqualityObservationalCriteriaAnalyzer()

    src = "if x is None: pass\nif a == b: pass\nif c is d: pass\nif e is True: pass"
    cmp_result = analyzer.analyze_comparisons(src)
    assert cmp_result["parse_ok"]
    assert cmp_result["is_comparison_count"] >= 3, f"expected ≥3 is, got {cmp_result['is_comparison_count']}"
    assert cmp_result["eq_comparison_count"] >= 1

    tree = ast.parse(src)
    is_cmps = analyzer.find_is_comparisons(tree)
    assert len(is_cmps) >= 3
    eq_cmps = analyzer.find_eq_comparisons(tree)
    assert len(eq_cmps) >= 1

    compare_node = next(n for n in ast.walk(tree) if isinstance(n, ast.Compare))
    kind = analyzer.classify_comparison(compare_node)
    assert kind in (ComparisonKind.IDENTITY, ComparisonKind.EQUALITY, ComparisonKind.IDENTITY_NEGATIVE)

    risk_src = "if x is 42: pass\nif y is 'abc': pass"
    risks = analyzer.check_interning_risks(risk_src)
    assert len(risks) >= 2, f"expected ≥2 risks, got {risks}"

    full_report = analyzer.build_comparison_report(src)
    assert full_report["parse_ok"]
    assert full_report["is_comparison_count"] >= 3
    assert full_report["correct_is_usage_count"] >= 2  # is None, is True

    cache_stats = analyzer.get_cache_stats()
    assert cache_stats["parse_cache_size"] >= 1

    # --- Witness ---
    witness = IdentityEqualityObservationalCriteriaWitness()

    obj1 = [1, 2]
    obj2 = [1, 2]  # equal but not identical
    obj3 = obj1    # alias

    r_is = witness.witness_comparison(obj1, obj3, OP_IS)
    assert r_is.identity_result is True
    assert r_is.diverges is False

    r_eq = witness.witness_comparison(obj1, obj2, OP_EQ)
    assert r_eq.equality_result is True
    assert r_eq.identity_result is False
    assert r_eq.diverges is True

    r_id_check = witness.record_identity_check(obj1, obj2, result=False)
    assert r_id_check.identity_result is False
    assert r_id_check.diverges is True  # False != True (eq_result)

    r_eq_check = witness.record_equality_check(obj1, obj2, result=True)
    assert r_eq_check.equality_result is True
    assert r_eq_check.diverges is True  # False != True

    divergences = witness.find_identity_equality_divergence([obj1, obj2, obj3])
    assert len(divergences) >= 1, f"expected ≥1 divergence, got {divergences}"

    evidence = witness.generate_observational_evidence()
    assert evidence["channel"] == EVIDENCE_CHANNEL_NAME
    assert evidence["statistics"]["total_comparisons"] >= 4

    summary = witness.get_criteria_summary()
    assert summary["divergence_count"] >= 2

    # Interning smoke check: small int interning means 256 is 256
    x = 256
    y = 256
    r_intern = witness.witness_comparison(x, y, OP_IS)
    assert r_intern.identity_result is True  # CPython interns small ints

    print(f"[{_ANALYSIS_CHANNEL}] smoke test PASSED ✓")
    print(f"  coordinator report: {report}")
    print(f"  analyzer cache: {cache_stats}")
    print(f"  witness summary: {summary}")


if __name__ == "__main__":
    _smoke_test()
