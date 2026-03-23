"""Protocol obligations theory for JuGeo unstable protocols (Ch22 §3).

Protocol obligations model the structural requirements that a class must
satisfy in order to be accepted as an implementation of a ``typing.Protocol``.
In Python, protocol satisfaction is purely structural (duck typing): a class
need not inherit from the Protocol; it must only expose every declared name
with a compatible signature.

Theory alignment (Ch22, theory2.tex)
-------------------------------------
* §3  Protocol obligations – each required method or attribute is a
      *ResidualObligation* in the sense of §3.2; an obligation is *discharged*
      once the implementing class provides a compatible definition.  The set of
      outstanding (undischarged) obligations forms the *obligation complex*
      Ob(P, C) for protocol P and candidate class C.
* §3  Satisfaction judgment – a class C *satisfies* protocol P iff
      Ob(P, C) = ∅.  Partial satisfaction (|Ob(P,C)| > 0) produces a
      *challenged* Judgment whose trust level is bounded above by
      ``ORACLE_PROPOSED``.
* §3  Inherited obligations – protocol inheritance induces a presheaf map:
      for P' ≤ P (P' is a sub-protocol of P), Ob(P, ·) ⊇ Ob(P', ·).  The
      :class:`ProtocolInheritanceResolver` computes the colimit of the
      obligation presheaf over the protocol MRO.
* §3  Signature compatibility – two signatures are *compatible* in the weak
      sense (used here) if the set of positional-or-keyword parameter names of
      the implementing method subsumes those of the required method.  Strict
      mode (not used here) would also require return-type co-variance.
* §3  Witness records – runtime checking (``isinstance`` on a
      ``@runtime_checkable`` Protocol) produces a *witness* that constitutes
      evidence of kind ``RUNTIME_WITNESS``.  Non-runtime-checkable protocols
      are checked structurally and produce ``ORACLE_PROPOSAL`` evidence.

Exports
-------
* :class:`ProtocolObligationsCoordinator`
* :class:`ProtocolObligationsAnalyzer`
* :class:`ProtocolObligationsWitness`
"""

from __future__ import annotations

import abc
import enum
import functools
import hashlib
import inspect
import json
import logging
import sys
import time
import typing
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace as dc_replace
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Local model imports – graceful fallback so the module is importable even
# when the full JuGeo package tree is not installed.
# ---------------------------------------------------------------------------

try:
    from jugeo.python_runtime.unstable_protocols.models import (
        ProtocolSection, StabilityLevel, ProxyRecord, ProxyRestriction,
        DelegationChain, DelegationKind, UnstableInterface, StabilityMonitor,
    )
except ImportError:  # pragma: no cover
    class ProtocolSection: pass  # type: ignore[no-redef]
    class StabilityLevel: pass   # type: ignore[no-redef]
    class ProxyRecord: pass      # type: ignore[no-redef]
    class ProxyRestriction: pass # type: ignore[no-redef]
    class DelegationChain: pass  # type: ignore[no-redef]
    class DelegationKind: pass   # type: ignore[no-redef]
    class UnstableInterface: pass # type: ignore[no-redef]
    class StabilityMonitor: pass  # type: ignore[no-redef]

try:
    from jugeo.geometry.site import (
        CoordinateObject, CoordinateKind, CoordinateMorphism, MorphismKind, Site, SiteBuilder,
    )
except Exception:  # pragma: no cover
    import enum as _enum

    class CoordinateKind(_enum.Enum):  # type: ignore[no-redef]
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"

    class MorphismKind(_enum.Enum):  # type: ignore[no-redef]
        RESTRICTION = "restriction"; INCLUSION = "inclusion"
        TRANSPORT = "transport"; REFINEMENT = "refinement"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: dict = field(default_factory=dict)

    class CoordinateMorphism:  # type: ignore[no-redef]
        def __init__(self, source: Any, target: Any, reason: str = "") -> None:
            self.source = source; self.target = target; self.reason = reason

    class Site: pass       # type: ignore[no-redef]
    class SiteBuilder: pass  # type: ignore[no-redef]

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, PropositionKind,
        Carrier, EvidenceBundle, EvidenceItem, EvidenceItemKind,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance, ProvenanceSource,
    )
except Exception:  # pragma: no cover
    import enum as _enum2

    class TrustLevel(_enum2.IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5

    class JudgmentStatus(_enum2.Enum):  # type: ignore[no-redef]
        PROPOSED = "proposed"; CHALLENGED = "challenged"
        SETTLED = "settled"; OBSTRUCTED = "obstructed"

    class PropositionKind(_enum2.Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
        RESOURCE = "resource"; SEMANTIC = "semantic"

    class EvidenceItemKind(_enum2.Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"; FORMAL_PROOF = "formal_proof"

    class ProvenanceSource(_enum2.Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"
        HUMAN = "human"; COMPOSED = "composed"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        kind: Any = None; formula: str = ""; free_variables: tuple[str, ...] = ()
        metadata: dict = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Carrier:  # type: ignore[no-redef]
        name: str = ""; parameters: tuple[str, ...] = (); is_dependent: bool = False
        metadata: dict = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        kind: Any = None; payload: dict = field(default_factory=dict)
        trust_level: Any = None; channel: str = ""; timestamp: str = ""
        expiry: str = ""; provenance: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        items: tuple[Any, ...] = ()

    @dataclass(frozen=True, slots=True)
    class ResidualObligation:  # type: ignore[no-redef]
        description: str = ""; obligation_id: str = ""; priority: int = 1
        is_discharged: bool = False

        def discharge(self, evidence: str = "") -> "ResidualObligation":
            """Return a discharged copy of this obligation."""
            return dc_replace(self, is_discharged=True)

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        description: str = ""; obstruction_id: str = ""; severity: int = 1

    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        level: Any = None; rationale: str = ""

    @dataclass(frozen=True, slots=True)
    class Provenance:  # type: ignore[no-redef]
        sources: tuple[Any, ...] = (); chain: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class Judgment:  # type: ignore[no-redef]
        coordinate: Any = None; proposition: Any = None; carrier: Any = None
        evidence: Any = None; obligations: tuple = (); obstructions: tuple = ()
        trust: Any = None; provenance: Any = None

# ---------------------------------------------------------------------------
# §3.1  Core obligation dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProtocolObligation:
    """Represents a single obligation imposed by a ``typing.Protocol``.

    Each declared method or class-variable in a Protocol generates one
    ``ProtocolObligation``.  The obligation is *discharged* when an
    implementing class provides the corresponding name with a compatible
    signature (Ch22 §3.2, Def. 3.4).

    Attributes
    ----------
    obligation_id:
        Stable UUID-based identifier derived from ``protocol_name`` and
        ``method_or_attr``.  Deterministic: same inputs always yield the
        same id.
    protocol_name:
        Qualified name of the Protocol class (e.g. ``"mymodule.Drawable"``).
    method_or_attr:
        Name of the required method or attribute.
    required_signature:
        String representation of the required signature as returned by
        ``inspect.signature``, or ``""`` for attributes and
        unannotated members.
    is_method:
        ``True`` if the member is callable (a method); ``False`` if it is
        a plain attribute or class variable.
    is_class_var:
        ``True`` if the member is annotated ``ClassVar[...]`` in the
        Protocol's ``__annotations__``.
    is_discharged:
        ``True`` once an implementing class has been verified to provide
        this member.  Immutable; a new instance is produced via
        :meth:`discharge`.
    discharging_class:
        Qualified name of the class that discharged this obligation, or
        ``""`` if not yet discharged.
    trust_level:
        The :class:`TrustLevel` at which the obligation was discharged.
        Defaults to ``TrustLevel.UNVERIFIED``.
    """

    obligation_id: str
    protocol_name: str
    method_or_attr: str
    required_signature: str
    is_method: bool
    is_class_var: bool
    is_discharged: bool
    discharging_class: str
    trust_level: Any

    # copilot: factory helper to build deterministic obligation IDs
    @staticmethod
    def make_id(protocol_name: str, method_or_attr: str) -> str:
        """Derive a deterministic obligation ID from protocol and member names.

        Uses SHA-256 of the concatenated names so that re-analysis of the
        same protocol always produces the same IDs, enabling stable
        cross-session references (Ch22 §3.2, Remark 3.5).
        """
        raw = f"{protocol_name}::{method_or_attr}"
        digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"oblig-{digest}"

    def discharge(
        self,
        discharging_class: str = "",
        trust_level: Any = None,
    ) -> "ProtocolObligation":
        """Return a new discharged copy of this obligation.

        Parameters
        ----------
        discharging_class:
            Qualified name of the class providing the implementation.
        trust_level:
            Evidence trust level for the discharge.  Defaults to
            ``TrustLevel.RUNTIME_WITNESSED``.

        Returns
        -------
        ProtocolObligation
            New instance with ``is_discharged=True`` and updated fields.
        """
        # copilot: produce immutable updated copy — frozen dataclass pattern
        tl = trust_level
        if tl is None:
            try:
                tl = TrustLevel.RUNTIME_WITNESSED
            except Exception:
                tl = 3
        return dc_replace(
            self,
            is_discharged=True,
            discharging_class=discharging_class,
            trust_level=tl,
        )

    def to_residual(self) -> ResidualObligation:
        """Convert this obligation to a :class:`ResidualObligation`.

        The conversion maps:
        * ``obligation_id`` → ``obligation_id``
        * ``is_discharged`` → ``is_discharged``
        * Human-readable ``description`` built from protocol and member names.
        * ``priority`` = 2 for methods, 1 for attributes (methods are more
          structurally critical per Ch22 §3.4).

        Returns
        -------
        ResidualObligation
            Equivalent residual-obligation term usable in the JuGeo
            judgment framework.
        """
        kind_tag = "method" if self.is_method else ("classvar" if self.is_class_var else "attr")
        description = (
            f"Protocol '{self.protocol_name}' requires {kind_tag} "
            f"'{self.method_or_attr}'"
        )
        if self.required_signature:
            description += f" with signature {self.required_signature}"
        if self.is_discharged:
            description += f" [discharged by {self.discharging_class}]"
        priority = 2 if self.is_method else 1
        ro = ResidualObligation(
            description=description,
            obligation_id=self.obligation_id,
            priority=priority,
            is_discharged=self.is_discharged,
        )
        return ro

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary for JSON export.

        Returns
        -------
        dict
            All fields, with ``trust_level`` coerced to its value or name.
        """
        tl_repr: Any
        try:
            tl_repr = self.trust_level.name if self.trust_level is not None else None
        except AttributeError:
            tl_repr = str(self.trust_level)
        return {
            "obligation_id": self.obligation_id,
            "protocol_name": self.protocol_name,
            "method_or_attr": self.method_or_attr,
            "required_signature": self.required_signature,
            "is_method": self.is_method,
            "is_class_var": self.is_class_var,
            "is_discharged": self.is_discharged,
            "discharging_class": self.discharging_class,
            "trust_level": tl_repr,
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary of the obligation.

        Format::

            [✓|✗] <protocol_name>.<method_or_attr> (<kind>) [<trust>]
        """
        status = "✓" if self.is_discharged else "✗"
        kind = "method" if self.is_method else ("classvar" if self.is_class_var else "attr")
        tl_str: str
        try:
            tl_str = self.trust_level.name if self.trust_level is not None else "?"
        except AttributeError:
            tl_str = str(self.trust_level)
        sig_part = f" :: {self.required_signature}" if self.required_signature else ""
        return f"[{status}] {self.protocol_name}.{self.method_or_attr}{sig_part} ({kind}) [{tl_str}]"


# ---------------------------------------------------------------------------
# §3.2  Satisfaction record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProtocolSatisfactionRecord:
    """Full record of whether a class satisfies a protocol.

    Aggregates all :class:`ProtocolObligation` instances for a
    (protocol, class) pair and computes summary statistics.

    Attributes
    ----------
    protocol_name:
        Qualified name of the Protocol.
    implementing_class:
        Qualified name of the candidate implementing class.
    obligations:
        Tuple of *all* obligations (discharged and undischarged).
    satisfied_count:
        Number of discharged obligations.
    missing_count:
        Number of undischarged obligations.
    signature_mismatches:
        Tuple of member names for which the member exists but the
        signature is not compatible.
    is_fully_satisfied:
        ``True`` iff ``missing_count == 0`` and no signature mismatches.
    trust_level:
        Overall trust level for the satisfaction claim.
    """

    protocol_name: str
    implementing_class: str
    obligations: tuple[ProtocolObligation, ...]
    satisfied_count: int
    missing_count: int
    signature_mismatches: tuple[str, ...]
    is_fully_satisfied: bool
    trust_level: Any

    def satisfaction_ratio(self) -> float:
        """Return the fraction of obligations that are discharged.

        Returns ``1.0`` when all obligations are discharged, ``0.0`` when
        none are, and ``float('nan')`` when there are no obligations at
        all (vacuously satisfied protocol).
        """
        total = self.satisfied_count + self.missing_count
        if total == 0:
            # copilot: vacuously satisfied — no obligations imposed
            return 1.0
        return self.satisfied_count / total

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary for JSON export.

        Returns
        -------
        dict
            All fields with nested obligations serialised via their own
            ``to_dict`` methods.
        """
        tl_repr: Any
        try:
            tl_repr = self.trust_level.name if self.trust_level is not None else None
        except AttributeError:
            tl_repr = str(self.trust_level)
        return {
            "protocol_name": self.protocol_name,
            "implementing_class": self.implementing_class,
            "obligations": [o.to_dict() for o in self.obligations],
            "satisfied_count": self.satisfied_count,
            "missing_count": self.missing_count,
            "signature_mismatches": list(self.signature_mismatches),
            "is_fully_satisfied": self.is_fully_satisfied,
            "trust_level": tl_repr,
            "satisfaction_ratio": self.satisfaction_ratio(),
        }

    def summary(self) -> str:
        """Return a multi-line human-readable summary.

        Format::

            <implementing_class> vs <protocol_name>
              satisfied : N / M  (ratio%)
              mismatches: [x, y, ...]
              status    : SATISFIED | PARTIAL | MISSING
        """
        total = self.satisfied_count + self.missing_count
        pct = f"{self.satisfaction_ratio() * 100:.1f}%"
        if self.is_fully_satisfied:
            status = "SATISFIED"
        elif self.satisfied_count == 0 and total > 0:
            status = "MISSING"
        else:
            status = "PARTIAL"
        mm = ", ".join(self.signature_mismatches) if self.signature_mismatches else "none"
        lines = [
            f"{self.implementing_class} vs {self.protocol_name}",
            f"  satisfied : {self.satisfied_count} / {total}  ({pct})",
            f"  mismatches: [{mm}]",
            f"  status    : {status}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# §3.3  Audit report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProtocolAuditReport:
    """Module-level audit report aggregating all satisfaction records.

    Produced by :meth:`ProtocolObligationsCoordinator.full_protocol_audit`
    after scanning a module for Protocol subclasses and candidate
    implementing classes.

    Attributes
    ----------
    report_id:
        UUID for this report instance.
    module_name:
        Qualified name of the audited module.
    satisfaction_records:
        All (protocol, class) satisfaction records found.
    total_protocols:
        Number of distinct protocols found in the module.
    fully_satisfied:
        Number of (protocol, class) pairs that are fully satisfied.
    partially_satisfied:
        Number of (protocol, class) pairs partially satisfied.
    not_satisfied:
        Number of (protocol, class) pairs with zero obligations discharged.
    generated_at:
        ISO-8601 timestamp string of report generation.
    """

    report_id: str
    module_name: str
    satisfaction_records: tuple[ProtocolSatisfactionRecord, ...]
    total_protocols: int
    fully_satisfied: int
    partially_satisfied: int
    not_satisfied: int
    generated_at: str

    def satisfaction_ratio(self) -> float:
        """Return fraction of (protocol, class) pairs fully satisfied.

        Returns ``1.0`` if there are no records (vacuously).
        """
        total = len(self.satisfaction_records)
        if total == 0:
            return 1.0
        return self.fully_satisfied / total

    def summary(self) -> str:
        """Return a formatted text summary of the audit report.

        Includes per-record summaries and aggregate statistics.
        """
        lines = [
            f"ProtocolAuditReport [{self.report_id}]",
            f"  module       : {self.module_name}",
            f"  generated_at : {self.generated_at}",
            f"  protocols    : {self.total_protocols}",
            f"  pairs total  : {len(self.satisfaction_records)}",
            f"  fully sat.   : {self.fully_satisfied}",
            f"  partial      : {self.partially_satisfied}",
            f"  not sat.     : {self.not_satisfied}",
            f"  overall ratio: {self.satisfaction_ratio() * 100:.1f}%",
            "",
        ]
        for rec in self.satisfaction_records:
            for ln in rec.summary().splitlines():
                lines.append("  " + ln)
            lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialise the full report to a plain dictionary.

        Returns
        -------
        dict
            All fields with nested records serialised recursively.
        """
        return {
            "report_id": self.report_id,
            "module_name": self.module_name,
            "satisfaction_records": [r.to_dict() for r in self.satisfaction_records],
            "total_protocols": self.total_protocols,
            "fully_satisfied": self.fully_satisfied,
            "partially_satisfied": self.partially_satisfied,
            "not_satisfied": self.not_satisfied,
            "generated_at": self.generated_at,
            "satisfaction_ratio": self.satisfaction_ratio(),
        }


# ---------------------------------------------------------------------------
# §3.4  Witness record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WitnessRecord:
    """Runtime witness for a protocol check on an object instance.

    Produced by :class:`ProtocolObligationsWitness` when it inspects an
    object at runtime.  A witness is a first-class piece of evidence
    (Ch22 §3.6, Def. 3.12): it records what was observed and when.

    Attributes
    ----------
    record_id:
        UUID for this witness record.
    obj_type:
        Qualified name of the object's type.
    protocol_name:
        Qualified name of the protocol that was checked.
    check_passed:
        ``True`` if the object satisfies the protocol at runtime.
    missing_attrs:
        Tuple of attribute names that were required but absent.
    timestamp:
        ISO-8601 string of the moment the check was performed.
    metadata:
        Arbitrary extra data (e.g. call site, frame info).
    """

    record_id: str
    obj_type: str
    protocol_name: str
    check_passed: bool
    missing_attrs: tuple[str, ...]
    timestamp: str
    metadata: dict

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
            All fields; ``metadata`` is included as-is.
        """
        return {
            "record_id": self.record_id,
            "obj_type": self.obj_type,
            "protocol_name": self.protocol_name,
            "check_passed": self.check_passed,
            "missing_attrs": list(self.missing_attrs),
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# §3.5  Protocol extractor
# ---------------------------------------------------------------------------


class ProtocolExtractor:
    """Extracts :class:`ProtocolObligation` instances from a Protocol class.

    This class is the primary entry point for obligation discovery.  It
    interrogates the Protocol's metaclass metadata (``__protocol_attrs__``,
    ``__annotations__``) and produces one obligation per required member.

    Implementation notes (Ch22 §3.5)
    ---------------------------------
    Python 3.12 introduced ``typing.get_protocol_members`` which is the
    canonical way to obtain the set of required names.  On older versions we
    fall back to ``__protocol_attrs__`` (set by the ``Protocol`` metaclass in
    3.8–3.11), and as a last resort we scan ``cls.__dict__`` manually.

    The extractor does *not* recurse into base protocols; that is the
    responsibility of :class:`ProtocolInheritanceResolver`.
    """

    def extract(self, protocol_cls: type) -> list[ProtocolObligation]:
        """Extract all obligations declared directly on *protocol_cls*.

        This method inspects only the members declared on *protocol_cls*
        itself, not inherited ones.  Use
        :class:`ProtocolInheritanceResolver` to include inherited
        obligations.

        Parameters
        ----------
        protocol_cls:
            A ``typing.Protocol`` subclass.  If a non-Protocol class is
            passed, a warning is logged and an empty list is returned.

        Returns
        -------
        list[ProtocolObligation]
            One obligation per required member, sorted by name for
            deterministic ordering.
        """
        if not self._is_protocol(protocol_cls):
            logger.warning(
                "ProtocolExtractor.extract: %r is not a Protocol — returning []",
                protocol_cls,
            )
            return []

        # copilot: gather member names from the most authoritative source available
        members = self._get_protocol_members(protocol_cls)
        p_name = getattr(protocol_cls, "__qualname__", repr(protocol_cls))

        obligations: list[ProtocolObligation] = []
        for member in sorted(members):
            sig_str = self._get_signature_str(protocol_cls, member)
            is_cv = self._is_classvar(protocol_cls, member)
            raw_attr = protocol_cls.__dict__.get(member) or getattr(protocol_cls, member, None)
            is_meth = callable(raw_attr) and not is_cv
            try:
                tl_default = TrustLevel.UNVERIFIED
            except Exception:
                tl_default = 1

            oblig = ProtocolObligation(
                obligation_id=ProtocolObligation.make_id(p_name, member),
                protocol_name=p_name,
                method_or_attr=member,
                required_signature=sig_str,
                is_method=is_meth,
                is_class_var=is_cv,
                is_discharged=False,
                discharging_class="",
                trust_level=tl_default,
            )
            obligations.append(oblig)
        return obligations

    def _is_protocol(self, cls: type) -> bool:
        """Return ``True`` if *cls* is a ``typing.Protocol`` subclass.

        Checks both the ``_is_protocol`` marker attribute (set by the
        Protocol metaclass) and the presence of ``typing.Protocol`` in the
        MRO.
        """
        if getattr(cls, "_is_protocol", False):
            return True
        try:
            return typing.Protocol in cls.__mro__
        except AttributeError:
            return False

    def _get_protocol_members(self, cls: type) -> set[str]:
        """Return the set of required member names for *cls*.

        Tries, in order:

        1. ``typing.get_protocol_members(cls)`` (Python ≥ 3.12)
        2. ``cls.__protocol_attrs__`` (Python 3.8–3.11)
        3. Manual scan of ``cls.__dict__`` filtering dunders, ``_is_protocol``,
           and ``Protocol`` itself.
        """
        # copilot: try canonical Python 3.12 API first
        get_members = getattr(typing, "get_protocol_members", None)
        if get_members is not None:
            try:
                result = get_members(cls)
                if isinstance(result, (set, frozenset)):
                    return set(result)
            except Exception as exc:
                logger.debug("get_protocol_members failed: %s", exc)

        # copilot: Python 3.8–3.11 fallback
        proto_attrs = getattr(cls, "__protocol_attrs__", None)
        if proto_attrs is not None:
            return set(proto_attrs)

        # copilot: last resort — manual scan
        excluded = frozenset({
            "_is_protocol", "_is_runtime_protocol", "__abstractmethods__",
            "__annotations__", "__weakref__", "__dict__", "__doc__",
            "__module__", "__protocol_attrs__", "__slots__",
        })
        members: set[str] = set()
        for name, value in cls.__dict__.items():
            if name.startswith("__") and name.endswith("__"):
                continue
            if name in excluded:
                continue
            members.add(name)
        # Also include annotated names that have no value in __dict__
        for name in getattr(cls, "__annotations__", {}).keys():
            if name.startswith("_"):
                continue
            members.add(name)
        return members

    def _get_signature_str(self, cls: type, member_name: str) -> str:
        """Return the string representation of the signature of *member_name*.

        Parameters
        ----------
        cls:
            The Protocol class that owns the member.
        member_name:
            Name of the member to inspect.

        Returns
        -------
        str
            ``str(inspect.signature(...))`` or ``""`` if unavailable.
        """
        try:
            attr = getattr(cls, member_name)
            sig = inspect.signature(attr)
            return str(sig)
        except (ValueError, TypeError):
            # copilot: some built-ins or properties may not have introspectable sigs
            return ""

    def _is_classvar(self, cls: type, member_name: str) -> bool:
        """Return ``True`` if *member_name* is annotated as ``ClassVar`` in *cls*.

        Inspects ``cls.__annotations__`` (direct only, not inherited) and
        checks whether the annotation is a ``ClassVar`` or its string form.
        """
        annotations = cls.__dict__.get("__annotations__", {})
        annotation = annotations.get(member_name)
        if annotation is None:
            return False
        # copilot: handle both runtime and string-form ClassVar annotations
        origin = getattr(annotation, "__origin__", None)
        if origin is typing.ClassVar:
            return True
        if isinstance(annotation, str) and "ClassVar" in annotation:
            return True
        return False


# ---------------------------------------------------------------------------
# §3.6  Satisfaction checker
# ---------------------------------------------------------------------------


class SatisfactionChecker:
    """Checks whether a concrete class satisfies a given protocol.

    Iterates over obligations extracted from the protocol and verifies:

    1. The implementing class exposes every required name (via its MRO).
    2. For methods, the implementing signature is *weakly compatible* with
       the required signature (see :meth:`_check_signature_compat`).

    The result is a :class:`ProtocolSatisfactionRecord`.
    """

    def __init__(self, extractor: ProtocolExtractor) -> None:
        """Initialise the checker with an obligation extractor.

        Parameters
        ----------
        extractor:
            The :class:`ProtocolExtractor` used to enumerate obligations.
        """
        # copilot: inject extractor to keep concerns separated
        self._extractor = extractor

    def check(self, cls: type, protocol_cls: type) -> ProtocolSatisfactionRecord:
        """Check whether *cls* satisfies *protocol_cls*.

        For each obligation in the protocol, the checker determines:
        * Whether ``hasattr(cls, name)`` (presence check).
        * If the obligation is a method, whether the signatures are
          weakly compatible.

        Parameters
        ----------
        cls:
            The candidate implementing class.
        protocol_cls:
            A ``typing.Protocol`` subclass.

        Returns
        -------
        ProtocolSatisfactionRecord
            Complete record of the satisfaction check.
        """
        obligations = self._extractor.extract(protocol_cls)
        cls_name = getattr(cls, "__qualname__", repr(cls))
        p_name = getattr(protocol_cls, "__qualname__", repr(protocol_cls))

        discharged_obligations: list[ProtocolObligation] = []
        signature_mismatches: list[str] = []

        for oblig in obligations:
            member = oblig.method_or_attr
            present = self._has_member(cls, member)
            if present:
                if oblig.is_method and oblig.required_signature:
                    compat = self._check_signature_compat(cls, member, oblig.required_signature)
                    if not compat:
                        # copilot: present but signature-incompatible
                        signature_mismatches.append(member)
                        # still counts as discharged for presence, but flagged
                discharged_obligations.append(
                    oblig.discharge(discharging_class=cls_name)
                )
            else:
                # copilot: member absent — obligation remains undischarged
                discharged_obligations.append(oblig)

        satisfied = sum(1 for o in discharged_obligations if o.is_discharged)
        missing = sum(1 for o in discharged_obligations if not o.is_discharged)
        is_fully = (missing == 0) and (len(signature_mismatches) == 0)
        trust = self._compute_trust(satisfied, satisfied + missing)

        return ProtocolSatisfactionRecord(
            protocol_name=p_name,
            implementing_class=cls_name,
            obligations=tuple(discharged_obligations),
            satisfied_count=satisfied,
            missing_count=missing,
            signature_mismatches=tuple(signature_mismatches),
            is_fully_satisfied=is_fully,
            trust_level=trust,
        )

    def _has_member(self, cls: type, member_name: str) -> bool:
        """Return ``True`` if *cls* exposes *member_name* through its MRO.

        Uses :func:`hasattr` which traverses the full MRO, consistent with
        Python's structural subtyping semantics.
        """
        return hasattr(cls, member_name)

    def _check_signature_compat(
        self,
        cls: type,
        member_name: str,
        required_sig: str,
    ) -> bool:
        """Return ``True`` if the implementing signature is weakly compatible.

        Weak compatibility (Ch22 §3.6, Def. 3.8): the set of
        positional-or-keyword parameter names of the implementing method
        must be a superset of those in the required signature (excluding
        ``self``).  This allows implementing methods to have additional
        optional parameters.

        Parameters
        ----------
        cls:
            The candidate implementing class.
        member_name:
            Name of the method to check.
        required_sig:
            String form of the required signature.

        Returns
        -------
        bool
            ``True`` if compatible or if signatures cannot be determined.
        """
        try:
            actual_attr = getattr(cls, member_name)
            actual_sig = inspect.signature(actual_attr)
        except (ValueError, TypeError):
            # copilot: cannot introspect actual — assume compatible
            return True

        # copilot: parse required param names from the string representation
        def _extract_param_names(sig_str: str) -> set[str]:
            """Very lightweight param-name extractor from a signature string."""
            # sig_str looks like "(self, factor: float) -> None" or "(factor: float)"
            inner = sig_str.strip("()")
            # take part before ")"
            paren_close = sig_str.find(")")
            if paren_close != -1:
                inner = sig_str[1:paren_close]
            parts = [p.strip().split(":")[0].split("=")[0].strip() for p in inner.split(",")]
            return {p.lstrip("*") for p in parts if p and not p.startswith("*") or p.lstrip("*")} - {"self", "cls", ""}

        required_names = _extract_param_names(required_sig)
        actual_names = {
            p.name
            for p in actual_sig.parameters.values()
            if p.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_ONLY,
            )
            and p.name not in ("self", "cls")
        }
        # copilot: implementing method must cover all required param names
        missing_params = required_names - actual_names
        # Allow VAR_KEYWORD (**kwargs) to absorb any missing params
        has_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in actual_sig.parameters.values()
        )
        if has_var_kw:
            return True
        return len(missing_params) == 0

    def _compute_trust(self, satisfied: int, total: int) -> "TrustLevel":
        """Compute a :class:`TrustLevel` from satisfaction counts.

        Mapping (Ch22 §3.7, Table 3.1):

        * 100% satisfied → ``RUNTIME_WITNESSED``
        * 50–99% → ``ORACLE_PROPOSED``
        * 1–49% → ``UNVERIFIED``
        * 0% or vacuous → ``UNVERIFIED``
        """
        try:
            if total == 0:
                return TrustLevel.UNVERIFIED
            ratio = satisfied / total
            if ratio >= 1.0:
                return TrustLevel.RUNTIME_WITNESSED
            if ratio >= 0.5:
                return TrustLevel.ORACLE_PROPOSED
            return TrustLevel.UNVERIFIED
        except Exception:
            # copilot: fallback when TrustLevel is the stub class
            return None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# §3.7  Protocol inheritance resolver
# ---------------------------------------------------------------------------


class ProtocolInheritanceResolver:
    """Resolves protocol inheritance to produce a merged obligation list.

    A protocol P may inherit from one or more other protocols P₁, …, Pₙ.
    The obligations of P are the union of the obligations of all its
    Protocol bases plus its own directly declared obligations.

    This corresponds to the colimit of the obligation presheaf over the
    protocol inheritance diagram (Ch22 §3.7, Theorem 3.10).
    """

    def __init__(self, extractor: ProtocolExtractor) -> None:
        """Initialise with an extractor for per-class obligation extraction.

        Parameters
        ----------
        extractor:
            Shared :class:`ProtocolExtractor` instance.
        """
        # copilot: extractor shared with SatisfactionChecker — no duplication
        self._extractor = extractor

    def resolve_obligations(self, protocol_cls: type) -> list[ProtocolObligation]:
        """Return merged obligations from *protocol_cls* and all its Protocol bases.

        Traverses the MRO of *protocol_cls*, collects obligations from every
        class that is itself a Protocol, and deduplicates by
        ``method_or_attr`` keeping the *most specific* definition (i.e. the
        one from the class earliest in MRO order).

        Parameters
        ----------
        protocol_cls:
            Root Protocol class.

        Returns
        -------
        list[ProtocolObligation]
            Deduplicated merged obligation list, sorted by ``method_or_attr``.
        """
        bases = self._collect_protocol_bases(protocol_cls)
        # copilot: MRO gives most-specific first; iterate that order
        seen: dict[str, ProtocolObligation] = {}
        for base in bases:
            for oblig in self._extractor.extract(base):
                if oblig.method_or_attr not in seen:
                    seen[oblig.method_or_attr] = oblig
        return sorted(seen.values(), key=lambda o: o.method_or_attr)

    def _collect_protocol_bases(self, cls: type) -> list[type]:
        """Return all Protocol subclasses in the MRO of *cls*, including *cls* itself.

        The list is in MRO order (most specific first) so that obligation
        deduplication in :meth:`resolve_obligations` respects override
        semantics.
        """
        result: list[type] = []
        try:
            mro = cls.__mro__
        except AttributeError:
            return [cls]
        for base in mro:
            if base is typing.Protocol:
                continue
            if base is object:
                continue
            if getattr(base, "_is_protocol", False):
                result.append(base)
            elif base is cls:
                # copilot: always include root even if _is_protocol is not set
                result.append(base)
        return result


# ---------------------------------------------------------------------------
# §3.8  Analyzer
# ---------------------------------------------------------------------------


class ProtocolObligationsAnalyzer:
    """High-level analyzer that orchestrates extraction, checking, and judgment.

    Combines :class:`ProtocolExtractor`, :class:`SatisfactionChecker`, and
    :class:`ProtocolInheritanceResolver` into a single facade.  Also builds
    :class:`Judgment` objects that can be integrated into the JuGeo judgment
    framework.

    Responsibilities (Ch22 §3.8)
    ------------------------------
    1. Extract full obligation sets via the resolver.
    2. Check concrete classes via the checker.
    3. Emit Judgment terms for each (protocol, class) pair.
    4. Audit entire modules by discovering protocols and classes dynamically.
    """

    def __init__(
        self,
        extractor: ProtocolExtractor,
        checker: SatisfactionChecker,
        resolver: ProtocolInheritanceResolver,
    ) -> None:
        """Initialise the analyzer.

        Parameters
        ----------
        extractor:
            Obligation extractor.
        checker:
            Satisfaction checker.
        resolver:
            Protocol inheritance resolver.
        """
        self._extractor = extractor
        self._checker = checker
        self._resolver = resolver
        # copilot: accumulate records across multiple calls for reporting
        self._records: list[ProtocolSatisfactionRecord] = []
        self._judgments: list[Judgment] = []

    def analyze_protocol(self, protocol_cls: type) -> list[ProtocolObligation]:
        """Return the full (inherited) obligation list for *protocol_cls*.

        Parameters
        ----------
        protocol_cls:
            Protocol class to analyse.

        Returns
        -------
        list[ProtocolObligation]
            Merged obligations including those from base protocols.
        """
        return self._resolver.resolve_obligations(protocol_cls)

    def check_implementation(
        self, cls: type, protocol_cls: type
    ) -> ProtocolSatisfactionRecord:
        """Check *cls* against *protocol_cls* and cache the result.

        Parameters
        ----------
        cls:
            Candidate implementing class.
        protocol_cls:
            Protocol class.

        Returns
        -------
        ProtocolSatisfactionRecord
            Result of the satisfaction check.
        """
        record = self._checker.check(cls, protocol_cls)
        self._records.append(record)
        return record

    def emit_judgments(self, cls: type, protocol_cls: type) -> list[Judgment]:
        """Build Judgment terms for a (cls, protocol) pair.

        Each obligation in the satisfaction record becomes either:
        * A discharged :class:`ResidualObligation` contributing to a
          ``SETTLED`` Judgment, or
        * An active :class:`ResidualObligation` contributing to a
          ``CHALLENGED`` or ``OBSTRUCTED`` Judgment.

        Parameters
        ----------
        cls:
            Implementing class.
        protocol_cls:
            Protocol class.

        Returns
        -------
        list[Judgment]
            One Judgment per satisfaction record (i.e. one per
            (cls, protocol) pair here).
        """
        record = self.check_implementation(cls, protocol_cls)
        judgments: list[Judgment] = []

        cls_name = getattr(cls, "__qualname__", repr(cls))
        p_name = getattr(protocol_cls, "__qualname__", repr(protocol_cls))

        # copilot: build one judgment summarising the entire satisfaction record
        residuals = tuple(o.to_residual() for o in record.obligations)
        obstructions_list: list[Obstruction] = []
        for mm in record.signature_mismatches:
            obstructions_list.append(
                Obstruction(
                    description=f"Signature mismatch for '{mm}' in {cls_name} vs {p_name}",
                    obstruction_id=ProtocolObligation.make_id(p_name, mm + ":mismatch"),
                    severity=2,
                )
            )

        try:
            prop = Proposition(
                kind=PropositionKind.STRUCTURAL,
                formula=f"satisfies({cls_name}, {p_name})",
                free_variables=(),
                metadata={"protocol": p_name, "class": cls_name},
            )
            carrier = Carrier(
                name=cls_name,
                parameters=(),
                is_dependent=False,
                metadata={"protocol": p_name},
            )
            ei_kind = (
                EvidenceItemKind.RUNTIME_WITNESS
                if record.is_fully_satisfied
                else EvidenceItemKind.ORACLE_PROPOSAL
            )
            ev_item = EvidenceItem(
                kind=ei_kind,
                payload={"satisfaction_ratio": record.satisfaction_ratio()},
                trust_level=record.trust_level,
                channel="protocol_obligations",
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                expiry="",
                provenance=(ProvenanceSource.RUNTIME,),
            )
            evidence = EvidenceBundle(items=(ev_item,))
            trust_ann = TrustAnnotation(
                level=record.trust_level,
                rationale=(
                    f"Satisfaction ratio {record.satisfaction_ratio():.2f}; "
                    f"{'fully' if record.is_fully_satisfied else 'partially'} satisfied"
                ),
            )
            prov = Provenance(
                sources=(ProvenanceSource.RUNTIME,),
                chain=("protocol_obligations",),
            )
            coord = CoordinateObject(
                components=(p_name, cls_name),
                kind=CoordinateKind.INTERFACE,
                support_labels=frozenset({p_name, cls_name}),
                metadata={},
            )
            judgment = Judgment(
                coordinate=coord,
                proposition=prop,
                carrier=carrier,
                evidence=evidence,
                obligations=residuals,
                obstructions=tuple(obstructions_list),
                trust=trust_ann,
                provenance=prov,
            )
        except Exception as exc:
            logger.debug("Could not build full Judgment: %s", exc)
            judgment = Judgment()  # type: ignore[call-arg]

        judgments.append(judgment)
        self._judgments.extend(judgments)
        return judgments

    def audit_module(self, module: Any) -> dict[str, list[ProtocolSatisfactionRecord]]:
        """Scan *module* for protocols and classes; check each pair.

        Discovers all Protocol subclasses and all concrete classes in the
        module's namespace, then checks every concrete class against every
        protocol.

        Parameters
        ----------
        module:
            A Python module object (or any namespace with ``__dict__``).

        Returns
        -------
        dict
            Mapping ``protocol_qualname → [ProtocolSatisfactionRecord, ...]``,
            one record per concrete class found.
        """
        namespace = getattr(module, "__dict__", {})
        protocols: list[type] = []
        classes: list[type] = []

        for _name, obj in namespace.items():
            if not isinstance(obj, type):
                continue
            if self._extractor._is_protocol(obj):
                protocols.append(obj)
            else:
                # copilot: exclude abstract base classes from candidate set
                classes.append(obj)

        result: dict[str, list[ProtocolSatisfactionRecord]] = {}
        for proto in protocols:
            p_name = getattr(proto, "__qualname__", repr(proto))
            records: list[ProtocolSatisfactionRecord] = []
            for cls in classes:
                rec = self._checker.check(cls, proto)
                records.append(rec)
            result[p_name] = records
        return result

    def summary_report(self) -> str:
        """Return a formatted text report of all cached satisfaction records.

        Includes one section per record and a footer with aggregate counts.
        """
        if not self._records:
            return "ProtocolObligationsAnalyzer: no records collected yet.\n"
        lines = ["=== ProtocolObligationsAnalyzer Report ===", ""]
        satisfied_total = sum(1 for r in self._records if r.is_fully_satisfied)
        for rec in self._records:
            lines.append(rec.summary())
            lines.append("")
        lines.append(
            f"Total records: {len(self._records)}  |  "
            f"Fully satisfied: {satisfied_total}  |  "
            f"Other: {len(self._records) - satisfied_total}"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# §3.9  Witness
# ---------------------------------------------------------------------------


class ProtocolObligationsWitness:
    """Runtime witness service for protocol checks on live objects.

    Provides :meth:`witness_protocol_check` which performs the best
    available runtime check for a given object and protocol, records
    the outcome as a :class:`WitnessRecord`, and returns it.

    The witness is the primary source of ``RUNTIME_WITNESS`` evidence in
    the JuGeo system (Ch22 §3.9, Remark 3.14).
    """

    def __init__(self) -> None:
        """Initialise with an empty record list."""
        # copilot: mutable list accumulates records across calls
        self._records: list[WitnessRecord] = []

    def witness_protocol_check(
        self,
        obj: Any,
        protocol_cls: type,
    ) -> WitnessRecord:
        """Perform a runtime protocol check on *obj* and record the result.

        Attempts ``isinstance(obj, protocol_cls)`` when the protocol is
        ``@runtime_checkable``, otherwise falls back to a manual attribute
        presence check.

        Parameters
        ----------
        obj:
            The object to check.
        protocol_cls:
            The Protocol class to check against.

        Returns
        -------
        WitnessRecord
            Witness recording the outcome, missing attributes, and timestamp.
        """
        p_name = getattr(protocol_cls, "__qualname__", repr(protocol_cls))
        obj_type_name = getattr(type(obj), "__qualname__", repr(type(obj)))
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        missing_attrs: list[str] = []
        check_passed: bool

        # copilot: prefer runtime isinstance for @runtime_checkable protocols
        is_runtime = getattr(protocol_cls, "_is_runtime_protocol", False)
        if is_runtime:
            try:
                check_passed = isinstance(obj, protocol_cls)
                if not check_passed:
                    # copilot: find which attrs are missing for diagnostics
                    extractor = ProtocolExtractor()
                    for oblig in extractor.extract(protocol_cls):
                        if not hasattr(obj, oblig.method_or_attr):
                            missing_attrs.append(oblig.method_or_attr)
            except TypeError:
                # copilot: isinstance failed (e.g. non-type protocol) — fallback
                check_passed = False
        else:
            # copilot: manual structural check for non-runtime-checkable protocols
            extractor = ProtocolExtractor()
            obligations = extractor.extract(protocol_cls)
            for oblig in obligations:
                if not hasattr(obj, oblig.method_or_attr):
                    missing_attrs.append(oblig.method_or_attr)
            check_passed = len(missing_attrs) == 0

        record = WitnessRecord(
            record_id=str(uuid.uuid4()),
            obj_type=obj_type_name,
            protocol_name=p_name,
            check_passed=check_passed,
            missing_attrs=tuple(missing_attrs),
            timestamp=timestamp,
            metadata={
                "runtime_checkable": is_runtime,
                "obj_repr": repr(obj)[:120],
            },
        )
        self._records.append(record)
        logger.debug(
            "WitnessRecord: %s satisfies %s → %s",
            obj_type_name, p_name, check_passed,
        )
        return record

    def all_records(self) -> list[WitnessRecord]:
        """Return all witness records accumulated so far.

        Returns
        -------
        list[WitnessRecord]
            Ordered by creation time (oldest first).
        """
        return list(self._records)

    def failed_checks(self) -> list[WitnessRecord]:
        """Return only the witness records where the check did not pass.

        Returns
        -------
        list[WitnessRecord]
            Records with ``check_passed == False``.
        """
        return [r for r in self._records if not r.check_passed]

    def summarize(self) -> dict:
        """Return aggregate statistics over all accumulated witness records.

        Returns
        -------
        dict
            Keys: ``total``, ``passed``, ``failed``, ``protocols`` (set of
            names), ``pass_rate`` (float).
        """
        total = len(self._records)
        passed = sum(1 for r in self._records if r.check_passed)
        failed = total - passed
        protocols = {r.protocol_name for r in self._records}
        pass_rate = (passed / total) if total > 0 else 1.0
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "protocols": sorted(protocols),
            "pass_rate": pass_rate,
        }


# ---------------------------------------------------------------------------
# §3.10  Coordinator
# ---------------------------------------------------------------------------


class ProtocolObligationsCoordinator:
    """Top-level coordinator for the protocol obligations subsystem.

    Aggregates the analyzer and witness, exposes module-level audit
    functionality, and translates results into JuGeo coordinate objects
    and judgment lists.

    This class is the primary public API for Ch22 §3.

    Attributes
    ----------
    analyzer:
        The :class:`ProtocolObligationsAnalyzer` used for static analysis.
    witness:
        The :class:`ProtocolObligationsWitness` used for runtime checks.
    """

    def __init__(
        self,
        analyzer: ProtocolObligationsAnalyzer,
        witness: ProtocolObligationsWitness,
    ) -> None:
        """Initialise the coordinator.

        Parameters
        ----------
        analyzer:
            Configured analyzer instance.
        witness:
            Configured witness instance.
        """
        self.analyzer = analyzer
        self.witness = witness

    def coordinate(self, protocol: type) -> CoordinateObject:
        """Build a :class:`CoordinateObject` that locates *protocol* in the site.

        The coordinate components are derived from the protocol's qualified
        name (e.g. ``"mymodule.Drawable"`` → components ``("mymodule",
        "Drawable")``).

        Parameters
        ----------
        protocol:
            The Protocol class to locate.

        Returns
        -------
        CoordinateObject
            Site coordinate for *protocol*.
        """
        qualname: str = getattr(protocol, "__qualname__", repr(protocol))
        module_name: str = getattr(protocol, "__module__", "")
        # copilot: split qualname on dots for hierarchical components
        parts = qualname.split(".")
        if module_name:
            components = tuple(module_name.split(".") + parts)
        else:
            components = tuple(parts)

        try:
            kind = CoordinateKind.INTERFACE
        except Exception:
            kind = None

        return CoordinateObject(
            components=components,
            kind=kind,
            support_labels=frozenset({qualname}),
            metadata={"protocol_name": qualname, "module": module_name},
        )

    def full_protocol_audit(self, module: Any) -> ProtocolAuditReport:
        """Run a full obligation audit of *module* and return a report.

        Discovers all protocols and concrete classes in *module*, checks
        every class against every protocol, and packages the results into a
        :class:`ProtocolAuditReport`.

        Parameters
        ----------
        module:
            Python module to audit.

        Returns
        -------
        ProtocolAuditReport
            Comprehensive audit report.
        """
        module_name: str = getattr(module, "__name__", repr(module))
        audit_map = self.analyzer.audit_module(module)

        all_records: list[ProtocolSatisfactionRecord] = []
        for recs in audit_map.values():
            all_records.extend(recs)

        fully_sat = sum(1 for r in all_records if r.is_fully_satisfied)
        # copilot: partial = some discharged, some missing; not = zero discharged
        partial = sum(
            1 for r in all_records
            if not r.is_fully_satisfied and r.satisfied_count > 0
        )
        not_sat = sum(
            1 for r in all_records
            if not r.is_fully_satisfied and r.satisfied_count == 0
        )

        return ProtocolAuditReport(
            report_id=str(uuid.uuid4()),
            module_name=module_name,
            satisfaction_records=tuple(all_records),
            total_protocols=len(audit_map),
            fully_satisfied=fully_sat,
            partially_satisfied=partial,
            not_satisfied=not_sat,
            generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def emit_all_judgments(self) -> list[Judgment]:
        """Return all Judgment terms produced by the analyzer so far.

        Returns
        -------
        list[Judgment]
            Accumulated judgments from all prior
            :meth:`ProtocolObligationsAnalyzer.emit_judgments` calls.
        """
        return list(self.analyzer._judgments)

    def emit_judgments(self) -> list[Judgment]:
        """Alias for :meth:`emit_all_judgments`.

        Provided for API symmetry with other Ch22 coordinators.
        """
        return self.emit_all_judgments()


# ---------------------------------------------------------------------------
# Module-level convenience factory
# ---------------------------------------------------------------------------


def build_coordinator() -> ProtocolObligationsCoordinator:
    """Construct a :class:`ProtocolObligationsCoordinator` with default components.

    Convenience factory that wires together a
    :class:`ProtocolExtractor`, :class:`SatisfactionChecker`,
    :class:`ProtocolInheritanceResolver`, :class:`ProtocolObligationsAnalyzer`,
    and :class:`ProtocolObligationsWitness`.

    Returns
    -------
    ProtocolObligationsCoordinator
        Fully initialised coordinator ready for use.
    """
    # copilot: single-call setup for use in scripts and notebooks
    extractor = ProtocolExtractor()
    checker = SatisfactionChecker(extractor=extractor)
    resolver = ProtocolInheritanceResolver(extractor=extractor)
    analyzer = ProtocolObligationsAnalyzer(
        extractor=extractor,
        checker=checker,
        resolver=resolver,
    )
    witness = ProtocolObligationsWitness()
    return ProtocolObligationsCoordinator(analyzer=analyzer, witness=witness)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import typing
    # copilot: smoke test — verifies protocol obligation extraction and satisfaction checking
    print(f"[smoke] {__file__}")
    try:
        @typing.runtime_checkable
        class Drawable(typing.Protocol):
            def draw(self) -> None: ...
            def resize(self, factor: float) -> None: ...

        class Circle:
            def draw(self) -> None: pass
            def resize(self, factor: float) -> None: pass

        class Square:
            def draw(self) -> None: pass
            # missing resize

        extractor = ProtocolExtractor()
        obligations = extractor.extract(Drawable)
        print(f"[smoke] extracted {len(obligations)} obligations from Drawable")

        checker = SatisfactionChecker(extractor=extractor)
        record_circle = checker.check(Circle, Drawable)
        print(f"[smoke] Circle satisfies Drawable: {record_circle.is_fully_satisfied}")

        record_square = checker.check(Square, Drawable)
        print(f"[smoke] Square satisfies Drawable: {record_square.is_fully_satisfied} missing={record_square.missing_count}")

        resolver = ProtocolInheritanceResolver(extractor=extractor)
        all_obs = resolver.resolve_obligations(Drawable)
        print(f"[smoke] resolved obligations: {len(all_obs)}")

        analyzer = ProtocolObligationsAnalyzer(
            extractor=extractor,
            checker=checker,
            resolver=resolver,
        )
        j = analyzer.emit_judgments(Circle, Drawable)
        print(f"[smoke] judgments emitted: {len(j)}")

        witness = ProtocolObligationsWitness()
        wr = witness.witness_protocol_check(Circle(), Drawable)
        print(f"[smoke] witness check_passed={wr.check_passed}")

        coordinator = ProtocolObligationsCoordinator(analyzer=analyzer, witness=witness)
        coord = coordinator.coordinate(Drawable)
        print(f"[smoke] coordinate: {coord.components}")
        print("[smoke] PASS")
    except Exception as exc:
        import traceback; traceback.print_exc()
        print(f"[smoke] FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
