"""Delegation-chain analysis for JuGeo unstable protocols (Ch22 §2).

Delegation chains as morphism sequences in the coordinate category,
cycle detection, repair-target localisation, and runtime witnessing.

Theory alignment (Ch22, theory2.tex)
-------------------------------------
* §2  Delegation chains – a delegation chain is a composable sequence of
      coordinate morphisms

          f₀ : C₀ → C₁,  f₁ : C₁ → C₂,  …,  fₙ₋₁ : Cₙ₋₁ → Cₙ

      where each Cᵢ is a :class:`CoordinateObject` and each fᵢ is a
      :class:`CoordinateMorphism` annotated with a :class:`DelegationKind`.
      The *composite trust* of the chain is the product of the per-link
      trust factors, mirroring the topos-theoretic notion that evidence
      attenuates as it passes through intermediaries.

* §2  Cycle detection – a cycle in the delegation graph corresponds to a
      non-acyclic diagram; such diagrams cannot be used to build sheaves
      (they violate the separation axiom for sites).  The tracer detects
      cycles via the standard visited-set algorithm (identity-based) and
      records the first repeated node as ``cycle_at``.

* §2  Repair-target localisation – given a broken method *m* observed at
      the chain head, the correct repair site is the unique link *k* such
      that Cₖ's underlying class defines *m* locally (i.e. ``m`` appears in
      ``type(Cₖ).__dict__``).  Patching a proxy rather than the definition
      site creates a *local section* that violates the gluing axiom; this
      module prevents that mistake.

* §2  Runtime witnessing – a :class:`DelegationChainsWitness` observes
      actual dispatch paths at runtime and compares them against the
      *expected path* registered a priori.  Any deviation constitutes an
      *unexpected hop*, which is recorded as an :class:`EvidenceItem` with
      kind ``RUNTIME_WITNESS``.

* §2  Coordinator – the :class:`DelegationChainsCoordinator` ties together
      analysis and witnessing, producing :class:`CoordinateObject` instances
      for each analysed chain so that downstream sheaf machinery can
      consume the results.

Typical usage::

    detector = DelegationDetector()
    tracer   = ChainTracer(detector=detector)
    locator  = RepairTargetLocator()
    analyzer = DelegationChainsAnalyzer(
        detector=DelegationDetector(),
        tracer=ChainTracer(detector=DelegationDetector()),
        locator=RepairTargetLocator(),
    )
    result  = analyzer.analyze(my_obj)
    repairs = analyzer.find_repair_target(my_obj, "broken_method")
"""

from __future__ import annotations

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
# Model imports with fallbacks
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.unstable_protocols.models import (
        ProtocolSection,
        StabilityLevel,
        ProxyRecord,
        ProxyRestriction,
        DelegationChain,
        DelegationKind,
        UnstableInterface,
        StabilityMonitor,
    )
except ImportError:  # pragma: no cover
    class ProtocolSection:  # type: ignore[no-redef]
        pass
    class StabilityLevel:  # type: ignore[no-redef]
        pass
    class ProxyRecord:  # type: ignore[no-redef]
        pass
    class ProxyRestriction:  # type: ignore[no-redef]
        pass
    class DelegationChain:  # type: ignore[no-redef]
        pass
    class DelegationKind:  # type: ignore[no-redef]
        pass
    class UnstableInterface:  # type: ignore[no-redef]
        pass
    class StabilityMonitor:  # type: ignore[no-redef]
        pass

try:
    from jugeo.geometry.site import (
        CoordinateObject,
        CoordinateKind,
        CoordinateMorphism,
        MorphismKind,
        Site,
        SiteBuilder,
    )
except Exception:  # pragma: no cover
    import enum

    class CoordinateKind(enum.Enum):  # type: ignore[no-redef]
        MODULE = "module"
        FUNCTION = "function"
        INTERFACE = "interface"
        TEST = "test"
        THEOREM = "theorem"
        REGION = "region"

    class MorphismKind(enum.Enum):  # type: ignore[no-redef]
        RESTRICTION = "restriction"
        INCLUSION = "inclusion"
        TRANSPORT = "transport"
        REFINEMENT = "refinement"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: dict = field(default_factory=dict)

    class CoordinateMorphism:  # type: ignore[no-redef]
        def __init__(self, source: Any, target: Any, reason: str = "") -> None:
            self.source = source
            self.target = target
            self.reason = reason

    class Site:  # type: ignore[no-redef]
        pass

    class SiteBuilder:  # type: ignore[no-redef]
        pass

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        JudgmentStatus,
        TrustLevel,
        Proposition,
        PropositionKind,
        Carrier,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        Provenance,
        ProvenanceSource,
    )
except Exception:  # pragma: no cover
    import enum

    class TrustLevel(enum.IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class JudgmentStatus(enum.Enum):  # type: ignore[no-redef]
        PROPOSED = "proposed"
        CHALLENGED = "challenged"
        SETTLED = "settled"
        OBSTRUCTED = "obstructed"

    class PropositionKind(enum.Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"
        BEHAVIORAL = "behavioral"
        RELATIONAL = "relational"
        RESOURCE = "resource"
        SEMANTIC = "semantic"

    class EvidenceItemKind(enum.Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"
        RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"
        FORMAL_PROOF = "formal_proof"

    class ProvenanceSource(enum.Enum):  # type: ignore[no-redef]
        SOLVER = "solver"
        RUNTIME = "runtime"
        ORACLE = "oracle"
        HUMAN = "human"
        COMPOSED = "composed"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        kind: Any = None
        formula: str = ""
        free_variables: tuple[str, ...] = ()
        metadata: dict = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Carrier:  # type: ignore[no-redef]
        name: str = ""
        parameters: tuple[str, ...] = ()
        is_dependent: bool = False
        metadata: dict = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        kind: Any = None
        payload: dict = field(default_factory=dict)
        trust_level: Any = None
        channel: str = ""
        timestamp: str = ""
        expiry: str = ""
        provenance: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        items: tuple[Any, ...] = ()

    @dataclass(frozen=True, slots=True)
    class ResidualObligation:  # type: ignore[no-redef]
        description: str = ""
        obligation_id: str = ""
        priority: int = 1
        is_discharged: bool = False

        def discharge(self, evidence: str = "") -> "ResidualObligation":
            return dc_replace(self, is_discharged=True)

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        description: str = ""
        obstruction_id: str = ""
        severity: int = 1

    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        level: Any = None
        rationale: str = ""

    @dataclass(frozen=True, slots=True)
    class Provenance:  # type: ignore[no-redef]
        sources: tuple[Any, ...] = ()
        chain: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class Judgment:  # type: ignore[no-redef]
        coordinate: Any = None
        proposition: Any = None
        carrier: Any = None
        evidence: Any = None
        obligations: tuple = ()
        obstructions: tuple = ()
        trust: Any = None
        provenance: Any = None


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Delegation attribute names that the detector will probe on an object.
_KNOWN_DELEGATION_ATTRS: tuple[str, ...] = (
    "delegate",
    "wrapped",
    "target",
    "_impl",
    "_delegate",
    "proxied",
    "__wrapped__",
)

#: Maximum trust factor for a single link; must be in (0, 1].
_MAX_TRUST_FACTOR: float = 1.0

#: Regular expression fragment used to identify forwarding patterns inside
#: ``__getattr__`` source code.
_GETATTR_FORWARD_PATTERNS: tuple[str, ...] = (
    "getattr(self.",
    "return getattr(self.",
    "self.__dict__[",
)


# ---------------------------------------------------------------------------
# 1.  DelegationLink
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DelegationLink:
    """A single directed edge in a delegation chain graph.

    Each link captures the relationship between a *source* object (the
    delegating party) and a *target* object (the actual implementor),
    together with metadata about argument/return-value transformations and a
    *trust_factor* in ``(0, 1]``.

    Theory alignment (Ch22 §2.1)
    -----------------------------
    A ``DelegationLink`` corresponds to a :class:`CoordinateMorphism`
    ``fᵢ : Cᵢ → Cᵢ₊₁`` in the coordinate category.  The *trust_factor*
    models the evidential attenuation introduced by the morphism: if ``fᵢ``
    transforms arguments or return values, evidence gathered at ``Cᵢ₊₁``
    cannot be directly transferred back to ``Cᵢ`` without accounting for
    the transformation.

    Attributes
    ----------
    link_id:
        A UUID4 string uniquely identifying this link within a session.
    source_qualname:
        Fully qualified Python name of the delegating class or callable
        (e.g. ``"mypackage.middleware.CachingProxy"``).
    target_qualname:
        Fully qualified Python name of the target class or callable.
    delegation_kind:
        A :class:`DelegationKind` value indicating *how* the delegation is
        implemented (e.g. ``__getattr__`` forwarding vs explicit composition).
    transforms_args:
        ``True`` when the link wraps or alters positional/keyword arguments
        before forwarding to the target.
    transforms_return:
        ``True`` when the link post-processes the target's return value.
    via_method:
        The Python dunder or ordinary method through which delegation is
        routed (e.g. ``"__getattr__"``, ``"delegate"``).
    coordinate_morphism_id:
        Opaque string referencing the :class:`CoordinateMorphism` object
        registered in the enclosing site (if any).
    trust_factor:
        A float in ``(0, 1]`` representing how much this link attenuates
        trust.  ``1.0`` means no attenuation (transparent forwarding);
        values below ``1.0`` indicate transformations that obscure evidence.
    """

    link_id: str
    source_qualname: str
    target_qualname: str
    delegation_kind: Any
    transforms_args: bool
    transforms_return: bool
    via_method: str
    coordinate_morphism_id: str
    trust_factor: float

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise the link to a plain ``dict`` suitable for JSON export.

        Returns a shallow copy of all fields, converting enum values to their
        ``value`` string if possible.

        Returns
        -------
        dict
            JSON-serialisable representation of this link.
        """
        # copilot: convert enum values gracefully so callers don't need to
        kind_val = (
            self.delegation_kind.value
            if hasattr(self.delegation_kind, "value")
            else str(self.delegation_kind)
        )
        return {
            "link_id": self.link_id,
            "source_qualname": self.source_qualname,
            "target_qualname": self.target_qualname,
            "delegation_kind": kind_val,
            "transforms_args": self.transforms_args,
            "transforms_return": self.transforms_return,
            "via_method": self.via_method,
            "coordinate_morphism_id": self.coordinate_morphism_id,
            "trust_factor": self.trust_factor,
        }

    def summary(self) -> str:
        """Return a compact human-readable description of this link.

        Example output::

            DelegationLink[MyProxy → MyImpl via __getattr__ (trust=0.90)]

        Returns
        -------
        str
            One-line summary string.
        """
        return (
            f"DelegationLink[{self.source_qualname} → {self.target_qualname}"
            f" via {self.via_method} (trust={self.trust_factor:.2f})]"
        )

    def composed_trust(self, other: "DelegationLink") -> float:
        """Compute the *composed* trust factor for this link followed by *other*.

        The composition rule is multiplicative: trust attenuates at each hop.
        This mirrors the sheaf-theoretic notion that evidence must pass through
        every restriction map to be valid at the base.

        Parameters
        ----------
        other:
            The next link in the chain.

        Returns
        -------
        float
            Product of ``self.trust_factor * other.trust_factor``, clamped
            to the interval ``(0, 1]``.
        """
        # copilot: clamp the product so floating-point drift never exceeds 1.0
        return min(_MAX_TRUST_FACTOR, self.trust_factor * other.trust_factor)


# ---------------------------------------------------------------------------
# 2.  ChainAnalysisResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainAnalysisResult:
    """Immutable summary of a full delegation chain analysis.

    Produced by :class:`ChainTracer` after following all delegation links
    reachable from a head object.

    Theory alignment (Ch22 §2.2)
    -----------------------------
    The result captures the *diagram* of coordinate morphisms discovered
    during tracing.  ``composite_trust`` is the product of all link trust
    factors — the evidential weight of the entire chain.  A cyclic diagram
    (``has_cycle=True``) violates the site's separation axiom and is flagged
    as non-repairable regardless of depth.

    Attributes
    ----------
    chain_id:
        UUID4 string identifying this analysis run.
    links:
        Ordered tuple of :class:`DelegationLink` objects, from head to tail.
    head:
        Qualname of the first (outermost) object in the chain.
    tail:
        Qualname of the last (innermost / most concrete) object in the chain.
    has_cycle:
        ``True`` when a cycle was detected during tracing.
    cycle_at:
        Qualname of the first object that was revisited (empty string if no
        cycle).
    depth:
        Number of links in the chain (``len(links)``).
    trust_level:
        A :class:`TrustLevel` value summarising the overall confidence in
        the chain.
    repair_target:
        Qualname of the object that should be patched when a method on the
        head is broken.
    composite_trust:
        Product of all link trust factors.
    """

    chain_id: str
    links: tuple["DelegationLink", ...]
    head: str
    tail: str
    has_cycle: bool
    cycle_at: str
    depth: int
    trust_level: Any
    repair_target: str
    composite_trust: float

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise the result to a plain ``dict``.

        Returns
        -------
        dict
            JSON-serialisable representation including all link dicts.
        """
        try:
            tl_val = self.trust_level.value if hasattr(self.trust_level, "value") else int(self.trust_level)
        except Exception:
            tl_val = str(self.trust_level)
        return {
            "chain_id": self.chain_id,
            "links": [lnk.to_dict() for lnk in self.links],
            "head": self.head,
            "tail": self.tail,
            "has_cycle": self.has_cycle,
            "cycle_at": self.cycle_at,
            "depth": self.depth,
            "trust_level": tl_val,
            "repair_target": self.repair_target,
            "composite_trust": self.composite_trust,
        }

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this chain analysis.

        Returns
        -------
        str
            Multi-line description suitable for logging or display.
        """
        lines = [
            f"ChainAnalysisResult chain_id={self.chain_id}",
            f"  head={self.head!r}  tail={self.tail!r}  depth={self.depth}",
            f"  has_cycle={self.has_cycle}  cycle_at={self.cycle_at!r}",
            f"  composite_trust={self.composite_trust:.4f}",
            f"  repair_target={self.repair_target!r}",
            f"  links ({len(self.links)}):",
        ]
        for i, lnk in enumerate(self.links):
            lines.append(f"    [{i}] {lnk.summary()}")
        return "\n".join(lines)

    def is_repairable(self) -> bool:
        """Determine whether this chain is amenable to automated repair.

        A chain is considered repairable if and only if:

        1. No cycle was detected (cyclic chains violate the separation axiom
           and require manual intervention).
        2. The depth does not exceed 5 (deep chains are brittle; patching
           them risks unintended side-effects in intermediate links).

        Returns
        -------
        bool
            ``True`` when both conditions are satisfied.
        """
        # copilot: depth threshold of 5 matches the heuristic in Ch22 §2.4
        return not self.has_cycle and self.depth <= 5


# ---------------------------------------------------------------------------
# 3.  WitnessRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WitnessRecord:
    """Immutable record of a single observed method dispatch.

    Produced by :class:`DelegationChainsWitness` when it observes a
    dispatch and compares it against the registered expected path.

    Theory alignment (Ch22 §2.5)
    -----------------------------
    Runtime witnesses provide :class:`EvidenceItem` objects with kind
    ``RUNTIME_WITNESS``.  Unexpected hops correspond to morphisms that were
    *not* part of the expected diagram; they may indicate either a stale
    static analysis or a genuine behavioural regression.

    Attributes
    ----------
    record_id:
        UUID4 string.
    method_name:
        Name of the method that was dispatched.
    dispatch_path:
        Ordered tuple of qualnames actually traversed during dispatch.
    timestamp:
        ISO-8601 UTC string at the moment of recording.
    hop_count:
        Total number of hops observed (``len(dispatch_path)``).
    unexpected_hops:
        Qualnames that appeared in ``dispatch_path`` but not in the expected
        path registered via
        :meth:`DelegationChainsWitness.register_expected_path`.
    trust_level:
        A :class:`TrustLevel` value; ``RUNTIME_WITNESSED`` when the path
        matched expectations, ``UNVERIFIED`` when unexpected hops occurred.
    """

    record_id: str
    method_name: str
    dispatch_path: tuple[str, ...]
    timestamp: str
    hop_count: int
    unexpected_hops: tuple[str, ...]
    trust_level: Any

    def to_dict(self) -> dict:
        """Serialise this record to a plain ``dict``.

        Returns
        -------
        dict
            JSON-serialisable representation of all fields.
        """
        try:
            tl_val = self.trust_level.value if hasattr(self.trust_level, "value") else int(self.trust_level)
        except Exception:
            tl_val = str(self.trust_level)
        return {
            "record_id": self.record_id,
            "method_name": self.method_name,
            "dispatch_path": list(self.dispatch_path),
            "timestamp": self.timestamp,
            "hop_count": self.hop_count,
            "unexpected_hops": list(self.unexpected_hops),
            "trust_level": tl_val,
        }


# ---------------------------------------------------------------------------
# 4.  DelegationDetector
# ---------------------------------------------------------------------------


class DelegationDetector:
    """Inspect Python objects to discover delegation patterns.

    The detector operates via two complementary strategies:

    1. **Attribute probing** — checks for well-known delegation attributes
       listed in :data:`_KNOWN_DELEGATION_ATTRS`.  When such an attribute is
       found, a :class:`DelegationLink` is created immediately.

    2. **Source analysis** — when ``__getattr__`` is defined on the object's
       class, the detector attempts to retrieve its source code and scan for
       ``getattr(self.X, name)`` forwarding patterns.  If the pattern is
       found, a link pointing to attribute ``X`` is created.

    Theory alignment (Ch22 §2.3)
    -----------------------------
    Both strategies correspond to discovering *implicit* coordinate morphisms
    that the programmer did not annotate explicitly.  The detector makes these
    implicit morphisms explicit so that the rest of the pipeline can reason
    about them formally.
    """

    def detect(self, obj: Any) -> list[DelegationLink]:
        """Detect all delegation links reachable from *obj*.

        Parameters
        ----------
        obj:
            Any Python object to inspect.

        Returns
        -------
        list[DelegationLink]
            Zero or more delegation links discovered on *obj*.  The list may
            contain duplicates if both strategies discover the same target;
            callers should de-duplicate by ``target_qualname`` if needed.
        """
        links: list[DelegationLink] = []
        source_qn = self._qualname(obj)

        # copilot: probe known delegation attributes first (fast path)
        for attr in _KNOWN_DELEGATION_ATTRS:
            try:
                target = object.__getattribute__(obj, attr)
            except AttributeError:
                continue
            # Exclude non-object values (strings, ints, None, booleans)
            if target is None or isinstance(target, (bool, int, float, str, bytes)):
                continue
            target_qn = self._qualname(target)
            lnk = self._build_link(
                source_qualname=source_qn,
                target_qualname=target_qn,
                kind=DelegationKind,
                via_method=attr,
            )
            links.append(lnk)
            logger.debug("detect: attribute probe found %s", lnk.summary())

        # copilot: analyse __getattr__ source for forwarding patterns
        if hasattr(type(obj), "__getattr__"):
            for tgt_attr in self._inspect_getattr(obj):
                # Avoid re-adding links already found by attribute probing
                already = any(lnk.via_method == tgt_attr for lnk in links)
                if not already:
                    try:
                        delegated = getattr(obj, tgt_attr, None)
                    except Exception:
                        delegated = None
                    target_qn = self._qualname(delegated) if delegated is not None else tgt_attr
                    lnk = self._build_link(
                        source_qualname=source_qn,
                        target_qualname=target_qn,
                        kind=DelegationKind,
                        via_method="__getattr__",
                    )
                    links.append(lnk)
                    logger.debug("detect: getattr analysis found %s", lnk.summary())

        return links

    def _inspect_getattr(self, obj: Any) -> list[str]:
        """Extract delegation-target attribute names from ``__getattr__`` source.

        Looks for patterns such as ``return getattr(self.X, name)`` or
        ``return self.X.__getattr__(name)`` inside the source code of
        ``type(obj).__getattr__``.

        Parameters
        ----------
        obj:
            The object whose class's ``__getattr__`` will be inspected.

        Returns
        -------
        list[str]
            Attribute names extracted from forwarding patterns.  May be empty
            if source cannot be retrieved or no pattern is found.
        """
        targets: list[str] = []
        getattr_method = getattr(type(obj), "__getattr__", None)
        if getattr_method is None:
            return targets
        try:
            src = inspect.getsource(getattr_method)
        except (OSError, TypeError):
            # copilot: source not available for built-ins or compiled classes
            return targets

        # copilot: scan line-by-line for forwarding patterns
        for line in src.splitlines():
            stripped = line.strip()
            for pattern in _GETATTR_FORWARD_PATTERNS:
                if pattern in stripped:
                    # Extract attribute name after "self."
                    # e.g. "return getattr(self._impl, name)" → "_impl"
                    try:
                        after_self = stripped.split("self.")[1]
                        attr_name = after_self.split(",")[0].split(")")[0].split("[")[0].strip()
                        if attr_name.isidentifier() and attr_name not in targets:
                            targets.append(attr_name)
                    except (IndexError, AttributeError):
                        pass
        return targets

    def _build_link(
        self,
        source_qualname: str,
        target_qualname: str,
        kind: Any,
        via_method: str,
    ) -> DelegationLink:
        """Construct a :class:`DelegationLink` with sensible defaults.

        Parameters
        ----------
        source_qualname:
            Qualname of the delegating object.
        target_qualname:
            Qualname of the delegate object.
        kind:
            A :class:`DelegationKind` value (or the class itself as a stub).
        via_method:
            The Python method or attribute name through which delegation occurs.

        Returns
        -------
        DelegationLink
            A freshly constructed link with a new UUID.
        """
        # copilot: determine if this link transforms args/return by checking
        #          whether via_method suggests a wrapping (anything other than
        #          bare attribute forwarding is treated as transforming)
        transforms = via_method not in ("__wrapped__", "wrapped", "proxied")
        morphism_id = hashlib.sha1(
            f"{source_qualname}:{target_qualname}:{via_method}".encode()
        ).hexdigest()[:16]

        # copilot: trust factor heuristic — lower trust if link transforms values
        trust = 0.85 if transforms else 1.0

        return DelegationLink(
            link_id=str(uuid.uuid4()),
            source_qualname=source_qualname,
            target_qualname=target_qualname,
            delegation_kind=kind,
            transforms_args=transforms,
            transforms_return=transforms,
            via_method=via_method,
            coordinate_morphism_id=morphism_id,
            trust_factor=trust,
        )

    def _qualname(self, obj: Any) -> str:
        """Return a best-effort fully qualified name for *obj*.

        Tries, in order:
        1. ``obj.__qualname__``
        2. ``obj.__name__``
        3. ``type(obj).__qualname__``
        4. ``repr(obj)`` truncated to 64 characters.

        Parameters
        ----------
        obj:
            Any Python object.

        Returns
        -------
        str
            A human-readable name string.
        """
        for attr in ("__qualname__", "__name__"):
            val = getattr(obj, attr, None)
            if isinstance(val, str) and val:
                return val
        # copilot: fall back to the type's qualname for instances
        type_qn = getattr(type(obj), "__qualname__", None)
        if isinstance(type_qn, str) and type_qn:
            return type_qn
        return repr(obj)[:64]


# ---------------------------------------------------------------------------
# 5.  ChainTracer
# ---------------------------------------------------------------------------


class ChainTracer:
    """Follow a delegation chain from a head object to its deepest target.

    The tracer uses :class:`DelegationDetector` to discover links at each
    hop, then recursively follows each link's target object until no further
    delegation is found, the maximum depth is reached, or a cycle is detected.

    Theory alignment (Ch22 §2.2)
    -----------------------------
    Tracing corresponds to computing the *path* in the coordinate category
    diagram.  Cycle detection ensures the tracing terminates; the visited-set
    is identity-based (``id()``), which correctly handles wrapper objects
    that override ``__eq__``.

    Parameters
    ----------
    detector:
        A :class:`DelegationDetector` instance used at each hop.
    max_depth:
        Maximum number of hops before the tracer stops (default 10).
    """

    def __init__(self, detector: DelegationDetector, max_depth: int = 10) -> None:
        self.detector = detector
        self.max_depth = max_depth

    def trace(self, obj: Any) -> ChainAnalysisResult:
        """Trace the full delegation chain starting from *obj*.

        The algorithm:

        1. Initialise ``visited`` (set of ``id()`` values) and ``current``
           pointing to *obj*.
        2. At each step, call :meth:`DelegationDetector.detect` on *current*.
        3. If no links are found, the chain ends at *current*.
        4. If *current* is already in ``visited``, record a cycle and stop.
        5. Otherwise, follow the first detected link to its target and repeat.

        Parameters
        ----------
        obj:
            The head object from which to start tracing.

        Returns
        -------
        ChainAnalysisResult
            Full analysis including all discovered links, cycle information,
            composite trust, and the recommended repair target.
        """
        chain_id = str(uuid.uuid4())
        head_qn = self.detector._qualname(obj)

        links: list[DelegationLink] = []
        visited_ids: set[int] = set()
        visited_qns: list[str] = []

        current = obj
        has_cycle = False
        cycle_at = ""

        for _ in range(self.max_depth):
            obj_id = id(current)
            current_qn = self.detector._qualname(current)

            # copilot: cycle detection — identity-based to handle __eq__ overrides
            if obj_id in visited_ids:
                has_cycle = True
                cycle_at = current_qn
                logger.warning("ChainTracer: cycle detected at %s", current_qn)
                break

            visited_ids.add(obj_id)
            visited_qns.append(current_qn)

            detected = self.detector.detect(current)
            if not detected:
                # copilot: no delegation found — this is the tail of the chain
                break

            # copilot: take the first (most specific) delegation link
            lnk = detected[0]
            links.append(lnk)

            next_obj = self._follow_delegation_target(current, lnk)
            if next_obj is None or next_obj is current:
                # copilot: could not resolve target object — stop here
                break

            current = next_obj

        tail_qn = self.detector._qualname(current) if not has_cycle else (cycle_at or head_qn)
        composite = self._compute_composite_trust(links)

        # copilot: choose trust level based on cycle status and composite trust
        if has_cycle:
            trust_level = TrustLevel.CONTRADICTED if hasattr(TrustLevel, "CONTRADICTED") else TrustLevel(0)  # type: ignore[call-arg]
        elif composite >= 0.9:
            trust_level = TrustLevel.RUNTIME_WITNESSED if hasattr(TrustLevel, "RUNTIME_WITNESSED") else TrustLevel(3)  # type: ignore[call-arg]
        elif composite >= 0.5:
            trust_level = TrustLevel.ORACLE_PROPOSED if hasattr(TrustLevel, "ORACLE_PROPOSED") else TrustLevel(2)  # type: ignore[call-arg]
        else:
            trust_level = TrustLevel.UNVERIFIED if hasattr(TrustLevel, "UNVERIFIED") else TrustLevel(1)  # type: ignore[call-arg]

        # copilot: repair target is tail unless cycle, in which case it's unknown
        repair_target = tail_qn if not has_cycle else ""

        return ChainAnalysisResult(
            chain_id=chain_id,
            links=tuple(links),
            head=head_qn,
            tail=tail_qn,
            has_cycle=has_cycle,
            cycle_at=cycle_at,
            depth=len(links),
            trust_level=trust_level,
            repair_target=repair_target,
            composite_trust=composite,
        )

    def _follow_delegation_target(self, obj: Any, link: "DelegationLink") -> Any:
        """Attempt to resolve the actual delegate object described by *link*.

        Tries each known delegation attribute in :data:`_KNOWN_DELEGATION_ATTRS`
        and also the ``via_method`` attribute of *link*.

        Parameters
        ----------
        obj:
            The object whose delegation target we want to retrieve.
        link:
            The :class:`DelegationLink` describing where to look.

        Returns
        -------
        Any or None
            The delegate object, or ``None`` if it cannot be resolved.
        """
        # copilot: try via_method first as it is the most specific hint
        for attr_name in (link.via_method,) + _KNOWN_DELEGATION_ATTRS:
            if attr_name.startswith("__") and attr_name.endswith("__"):
                # dunder — skip unless it's __wrapped__ which wraps the real object
                if attr_name != "__wrapped__":
                    continue
            try:
                candidate = object.__getattribute__(obj, attr_name)
                if candidate is not None and not isinstance(candidate, (bool, int, float, str, bytes)):
                    return candidate
            except AttributeError:
                continue
        return None

    def _compute_composite_trust(self, links: list["DelegationLink"]) -> float:
        """Multiply together all link trust factors.

        Parameters
        ----------
        links:
            Ordered list of delegation links.

        Returns
        -------
        float
            Product of all trust factors, or ``1.0`` for an empty list.
        """
        if not links:
            return 1.0
        result = 1.0
        for lnk in links:
            result *= lnk.trust_factor
        # copilot: clamp to avoid floating-point values marginally above 1.0
        return min(_MAX_TRUST_FACTOR, result)


# ---------------------------------------------------------------------------
# 6.  RepairTargetLocator
# ---------------------------------------------------------------------------


class RepairTargetLocator:
    """Determine the correct repair target for a broken method in a chain.

    When a method ``m`` appears to be broken on the *head* object, the actual
    definition site is typically NOT the head (which merely delegates).  This
    class identifies the chain link whose underlying class actually *defines*
    ``m`` in its own ``__dict__``, not just inheriting it through the MRO.

    Theory alignment (Ch22 §2.4)
    -----------------------------
    Repair corresponds to modifying the section ``P(Cₖ)`` at the unique
    coordinate ``Cₖ`` where the method is locally defined.  Patching an
    intermediate proxy violates the gluing axiom because the patched section
    is only valid on the sub-open-set covered by that proxy.
    """

    def locate(self, chain: "ChainAnalysisResult", method_name: str) -> str:
        """Find the qualname of the object that locally defines *method_name*.

        Iterates through chain links in order, checking each target qualname.
        Returns the first target whose underlying class defines ``method_name``
        in its own ``__dict__``.  Falls back to ``chain.tail`` if no local
        definition is found.

        Parameters
        ----------
        chain:
            A completed :class:`ChainAnalysisResult`.
        method_name:
            The name of the method whose definition site we want.

        Returns
        -------
        str
            Qualname of the class that should be patched, or ``chain.tail``
            if the method is not found locally in any link.
        """
        if not chain.links:
            # copilot: degenerate chain — the head is also the tail
            return chain.head

        for lnk in chain.links:
            if self._method_is_local(lnk.target_qualname, method_name):
                logger.debug(
                    "locate: method %r found locally at %s",
                    method_name,
                    lnk.target_qualname,
                )
                return lnk.target_qualname

        # copilot: method may be inherited throughout; default to tail
        logger.debug(
            "locate: method %r not found in any link; defaulting to tail %r",
            method_name,
            chain.tail,
        )
        return chain.tail

    def _method_is_local(self, qualname: str, method_name: str) -> bool:
        """Check whether *qualname* locally defines *method_name*.

        "Locally" means the method appears in ``cls.__dict__``, not merely
        in the class's MRO via inheritance.

        Parameters
        ----------
        qualname:
            Fully qualified class name to resolve.
        method_name:
            Name of the method to look up.

        Returns
        -------
        bool
            ``True`` if the class is found and ``method_name in cls.__dict__``.
        """
        cls = self._resolve_qualname(qualname)
        if cls is None:
            return False
        return method_name in cls.__dict__

    def _resolve_qualname(self, qualname: str) -> type | None:
        """Attempt to resolve a dotted qualname to a Python class.

        Searches all currently imported modules in ``sys.modules`` for a
        class matching the given qualname.

        Parameters
        ----------
        qualname:
            A fully qualified class name such as ``"mymodule.MyClass"``.

        Returns
        -------
        type or None
            The resolved class, or ``None`` if it cannot be found.
        """
        # copilot: walk through sys.modules to find the matching class
        parts = qualname.rsplit(".", 1)
        if len(parts) == 2:
            module_name, class_name = parts
            module = sys.modules.get(module_name)
            if module is not None:
                cls = getattr(module, class_name, None)
                if isinstance(cls, type):
                    return cls

        # copilot: try treating the entire qualname as a class name in each module
        for mod in sys.modules.values():
            if mod is None:
                continue
            candidate = getattr(mod, qualname.split(".")[-1], None)
            if isinstance(candidate, type):
                # Verify the qualname matches
                candidate_qn = getattr(candidate, "__qualname__", "")
                if candidate_qn == qualname or candidate_qn.endswith("." + qualname.split(".")[-1]):
                    return candidate
        return None


# ---------------------------------------------------------------------------
# 7.  DelegationChainsAnalyzer
# ---------------------------------------------------------------------------


class DelegationChainsAnalyzer:
    """High-level analyser that ties together detection, tracing, and repair.

    This class is the primary entry point for consumers who want to understand
    the delegation structure of a Python object and plan targeted repairs.

    Theory alignment (Ch22 §2.3, §2.4)
    -------------------------------------
    The analyser builds a catalogue of :class:`ChainAnalysisResult` objects
    (one per analysed object) and derives :class:`Judgment` instances from
    them.  Each judgment carries ``EvidenceItem`` objects derived from the
    chain's composite trust and repair-target information.

    Parameters
    ----------
    detector:
        A :class:`DelegationDetector` used internally for re-analysis.
    tracer:
        A :class:`ChainTracer` for following chains.
    locator:
        A :class:`RepairTargetLocator` for finding method definition sites.
    """

    def __init__(
        self,
        detector: DelegationDetector,
        tracer: ChainTracer,
        locator: RepairTargetLocator,
    ) -> None:
        self.detector = detector
        self.tracer = tracer
        self.locator = locator
        self._results: list[ChainAnalysisResult] = []
        self._judgments: list[Judgment] = []

    def analyze(self, obj: Any) -> ChainAnalysisResult:
        """Trace and record the delegation chain starting from *obj*.

        Parameters
        ----------
        obj:
            Any Python object to analyse.

        Returns
        -------
        ChainAnalysisResult
            The completed chain analysis.  Also appended to ``self._results``.
        """
        result = self.tracer.trace(obj)
        self._results.append(result)
        logger.info("DelegationChainsAnalyzer.analyze: %s", result.summary())
        return result

    def find_repair_target(self, obj: Any, method_name: str) -> str:
        """Analyse *obj* and return the qualname that should be patched.

        Combines :meth:`analyze` with :meth:`RepairTargetLocator.locate`.

        Parameters
        ----------
        obj:
            The head object.
        method_name:
            The method whose definition site we need.

        Returns
        -------
        str
            Qualname of the repair target.
        """
        result = self.analyze(obj)
        return self.locator.locate(result, method_name)

    def emit_judgments(self) -> list[Judgment]:
        """Build and return :class:`Judgment` objects for each analysed chain.

        Each judgment encodes:

        * A :class:`Proposition` whose formula describes the chain structure.
        * An :class:`EvidenceBundle` containing a single
          ``RUNTIME_WITNESS`` item derived from composite trust.
        * A :class:`TrustAnnotation` reflecting the chain's trust level.
        * A :class:`Provenance` recording that the evidence came from runtime
          analysis.

        Returns
        -------
        list[Judgment]
            One judgment per entry in ``self._results``.  Also extends
            ``self._judgments``.
        """
        new_judgments: list[Judgment] = []
        for result in self._results:
            # copilot: build a structural proposition describing the chain
            formula = (
                f"delegation_chain({result.head!r}, {result.tail!r}, "
                f"depth={result.depth}, cycle={result.has_cycle})"
            )
            prop = Proposition(
                kind=PropositionKind.STRUCTURAL if hasattr(PropositionKind, "STRUCTURAL") else None,
                formula=formula,
                free_variables=(),
                metadata={"chain_id": result.chain_id},
            )
            carrier = Carrier(
                name=result.head,
                parameters=(),
                is_dependent=False,
                metadata={"repair_target": result.repair_target},
            )
            evidence_item = EvidenceItem(
                kind=EvidenceItemKind.RUNTIME_WITNESS if hasattr(EvidenceItemKind, "RUNTIME_WITNESS") else None,
                payload={"composite_trust": result.composite_trust, "depth": result.depth},
                trust_level=result.trust_level,
                channel="delegation_analysis",
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                expiry="",
                provenance=(result.chain_id,),
            )
            bundle = EvidenceBundle(items=(evidence_item,))
            trust_ann = TrustAnnotation(
                level=result.trust_level,
                rationale=f"composite_trust={result.composite_trust:.4f}",
            )
            prov = Provenance(
                sources=(ProvenanceSource.RUNTIME if hasattr(ProvenanceSource, "RUNTIME") else None,),
                chain=(result.chain_id,),
            )
            obligations: tuple = ()
            if result.has_cycle:
                obl = ResidualObligation(
                    description="Resolve delegation cycle before repair is possible.",
                    obligation_id=str(uuid.uuid4()),
                    priority=1,
                    is_discharged=False,
                )
                obligations = (obl,)

            coord_obj = CoordinateObject(
                components=(result.head, result.tail),
                kind=CoordinateKind.INTERFACE if hasattr(CoordinateKind, "INTERFACE") else None,
                support_labels=frozenset({"delegation_chain"}),
                metadata={"chain_id": result.chain_id},
            )
            j = Judgment(
                coordinate=coord_obj,
                proposition=prop,
                carrier=carrier,
                evidence=bundle,
                obligations=obligations,
                obstructions=(),
                trust=trust_ann,
                provenance=prov,
            )
            new_judgments.append(j)
        self._judgments.extend(new_judgments)
        return new_judgments

    def summary_report(self) -> str:
        """Produce a formatted text report over all analysed chains.

        The report includes per-chain summaries plus aggregate statistics
        (total chains, number of cycles, mean composite trust).

        Returns
        -------
        str
            Multi-line plain-text report.
        """
        lines = [
            "=" * 72,
            "DelegationChainsAnalyzer — Summary Report",
            f"  Chains analysed: {len(self._results)}",
        ]
        cycles = sum(1 for r in self._results if r.has_cycle)
        lines.append(f"  Chains with cycles: {cycles}")
        if self._results:
            mean_trust = sum(r.composite_trust for r in self._results) / len(self._results)
            lines.append(f"  Mean composite trust: {mean_trust:.4f}")
        lines.append("=" * 72)
        for idx, result in enumerate(self._results):
            lines.append(f"\n--- Chain {idx + 1} ---")
            lines.append(result.summary())
        lines.append("=" * 72)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8.  DelegationChainsWitness
# ---------------------------------------------------------------------------


class DelegationChainsWitness:
    """Record and validate runtime method dispatch paths.

    Consumers instrument their dispatch machinery (e.g. inside ``__getattr__``
    or a middleware layer) to call :meth:`witness_dispatch` whenever a method
    is invoked.  The witness compares the actual path against the expected
    path registered via :meth:`register_expected_path` and records any
    deviations as unexpected hops.

    Theory alignment (Ch22 §2.5)
    -----------------------------
    Unexpected hops are the runtime counterpart of *obstructions* in the
    topos: they indicate that the observed diagram does not commute with the
    expected one, which may signal a regression or a stale static analysis.
    """

    def __init__(self) -> None:
        self._records: list[WitnessRecord] = []
        self._expected_paths: dict[str, list[str]] = {}

    def register_expected_path(self, method_name: str, expected: list[str]) -> None:
        """Register the expected dispatch path for *method_name*.

        Parameters
        ----------
        method_name:
            Name of the method (e.g. ``"process"``).
        expected:
            Ordered list of qualnames expected to be traversed during dispatch
            (e.g. ``["CachingProxy", "BusinessLogic"]``).
        """
        self._expected_paths[method_name] = list(expected)
        logger.debug(
            "DelegationChainsWitness: registered expected path for %r: %s",
            method_name,
            expected,
        )

    def witness_dispatch(self, method_name: str, dispatch_path: list[str]) -> WitnessRecord:
        """Record an observed dispatch and compare it against expectations.

        Parameters
        ----------
        method_name:
            The method that was dispatched.
        dispatch_path:
            Ordered list of qualnames actually traversed.

        Returns
        -------
        WitnessRecord
            Immutable record of this observation, including any unexpected hops.
            Also appended to ``self._records``.
        """
        expected = self._expected_paths.get(method_name, [])
        expected_set = set(expected)

        # copilot: unexpected hops = nodes in actual path not present in expected
        unexpected = tuple(qn for qn in dispatch_path if qn not in expected_set)

        # copilot: trust level depends on whether unexpected hops were found
        if not unexpected:
            tl = TrustLevel.RUNTIME_WITNESSED if hasattr(TrustLevel, "RUNTIME_WITNESSED") else TrustLevel(3)  # type: ignore[call-arg]
        else:
            tl = TrustLevel.UNVERIFIED if hasattr(TrustLevel, "UNVERIFIED") else TrustLevel(1)  # type: ignore[call-arg]

        record = WitnessRecord(
            record_id=str(uuid.uuid4()),
            method_name=method_name,
            dispatch_path=tuple(dispatch_path),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            hop_count=len(dispatch_path),
            unexpected_hops=unexpected,
            trust_level=tl,
        )
        self._records.append(record)
        if unexpected:
            logger.warning(
                "DelegationChainsWitness: unexpected hops for %r: %s",
                method_name,
                unexpected,
            )
        return record

    def all_records(self) -> list[WitnessRecord]:
        """Return all witness records collected so far.

        Returns
        -------
        list[WitnessRecord]
            Chronological list of all records.
        """
        return list(self._records)

    def unexpected_dispatches(self) -> list[WitnessRecord]:
        """Return records where at least one unexpected hop was observed.

        Returns
        -------
        list[WitnessRecord]
            Filtered list of records with non-empty ``unexpected_hops``.
        """
        return [r for r in self._records if r.unexpected_hops]

    def summarize(self) -> dict:
        """Produce aggregate statistics over all collected records.

        Returns
        -------
        dict
            A dict with keys:

            * ``total_records`` — total number of records.
            * ``unexpected_count`` — records with unexpected hops.
            * ``methods_observed`` — sorted list of distinct method names.
            * ``mean_hop_count`` — average hop count across all records.
            * ``unexpected_method_counts`` — mapping of method → count of
              unexpected-hop records for that method.
        """
        total = len(self._records)
        unexpected = self.unexpected_dispatches()
        methods = sorted({r.method_name for r in self._records})
        mean_hops = (
            sum(r.hop_count for r in self._records) / total if total > 0 else 0.0
        )
        # copilot: count unexpected dispatches per method for targeted debugging
        unexpected_by_method: dict[str, int] = defaultdict(int)
        for r in unexpected:
            unexpected_by_method[r.method_name] += 1
        return {
            "total_records": total,
            "unexpected_count": len(unexpected),
            "methods_observed": methods,
            "mean_hop_count": mean_hops,
            "unexpected_method_counts": dict(unexpected_by_method),
        }


# ---------------------------------------------------------------------------
# 9.  DelegationChainsCoordinator
# ---------------------------------------------------------------------------


class DelegationChainsCoordinator:
    """Orchestrate analysis, witnessing, and coordinate-object construction.

    The coordinator bridges the delegation-chain analysis layer with the
    geometry layer (:mod:`jugeo.geometry.site`) by producing
    :class:`CoordinateObject` instances for each analysed chain.  These
    objects can then be registered in a :class:`Site` and consumed by the
    sheaf machinery.

    Theory alignment (Ch22 §2.6)
    -----------------------------
    Each :class:`ChainAnalysisResult` maps to a :class:`CoordinateObject`
    whose ``components`` tuple encodes the qualnames of the head and tail
    (the two boundary objects of the chain).  The ``kind`` is always
    ``CoordinateKind.INTERFACE`` because a delegation chain is, from the
    site's perspective, an interface morphism between two regions.

    Parameters
    ----------
    analyzer:
        A :class:`DelegationChainsAnalyzer` instance.
    witness:
        A :class:`DelegationChainsWitness` instance.
    """

    def __init__(
        self,
        analyzer: DelegationChainsAnalyzer,
        witness: DelegationChainsWitness,
    ) -> None:
        self.analyzer = analyzer
        self.witness = witness

    def coordinate(self, chain: "ChainAnalysisResult") -> CoordinateObject:
        """Build a :class:`CoordinateObject` for *chain*.

        The resulting coordinate encodes the chain's head, tail, and depth
        as its ``components`` tuple.  The ``metadata`` dict carries the
        chain's ``chain_id``, ``composite_trust``, and ``has_cycle`` flag.

        Parameters
        ----------
        chain:
            A completed :class:`ChainAnalysisResult`.

        Returns
        -------
        CoordinateObject
            A new coordinate object suitable for registration in a site.
        """
        # copilot: encode depth in components so the site can route by depth
        components = (chain.head, chain.tail, f"depth:{chain.depth}")
        labels: frozenset[str] = frozenset({"delegation_chain"})
        if chain.has_cycle:
            labels = labels | frozenset({"cyclic"})
        if chain.is_repairable():
            labels = labels | frozenset({"repairable"})

        coord = CoordinateObject(
            components=components,
            kind=CoordinateKind.INTERFACE if hasattr(CoordinateKind, "INTERFACE") else None,
            support_labels=labels,
            metadata={
                "chain_id": chain.chain_id,
                "composite_trust": chain.composite_trust,
                "has_cycle": chain.has_cycle,
                "repair_target": chain.repair_target,
            },
        )
        logger.debug("DelegationChainsCoordinator.coordinate: %s", coord)
        return coord

    def trace_all_chains(self, obj: Any) -> list[ChainAnalysisResult]:
        """Detect all delegation targets from *obj* and trace each one.

        Unlike :meth:`DelegationChainsAnalyzer.analyze` which traces a single
        chain, this method first detects *all* links from *obj* and then
        traces a separate chain for each detected target.  This is useful
        when an object delegates to multiple backends (fan-out delegation).

        Parameters
        ----------
        obj:
            The head object to inspect.

        Returns
        -------
        list[ChainAnalysisResult]
            One result per detected delegation target.
        """
        links = self.analyzer.detector.detect(obj)
        if not links:
            # copilot: no delegation targets — return the trivial single-link chain
            return [self.analyzer.analyze(obj)]

        results: list[ChainAnalysisResult] = []
        seen_targets: set[str] = set()
        for lnk in links:
            if lnk.target_qualname in seen_targets:
                continue
            seen_targets.add(lnk.target_qualname)

            # copilot: try to resolve the target object for tracing
            target_obj = self.analyzer.tracer._follow_delegation_target(obj, lnk)
            if target_obj is None:
                # fall back to tracing from head with this link prepended
                partial_result = self.analyzer.analyze(obj)
                results.append(partial_result)
            else:
                result = self.analyzer.analyze(target_obj)
                results.append(result)

        logger.info(
            "DelegationChainsCoordinator.trace_all_chains: traced %d chains from %s",
            len(results),
            self.analyzer.detector._qualname(obj),
        )
        return results

    def locate_repair_targets(self, obj: Any) -> dict[str, str]:
        """Map each public method of *obj* to its repair target qualname.

        Iterates over ``dir(obj)`` filtering to public, callable attributes.
        For each such method, calls :meth:`DelegationChainsAnalyzer.find_repair_target`.

        Parameters
        ----------
        obj:
            The head object whose public API is inspected.

        Returns
        -------
        dict[str, str]
            Mapping of method name → repair target qualname.
        """
        repair_map: dict[str, str] = {}
        # copilot: inspect public callables only (skip dunders and private attrs)
        for name in dir(obj):
            if name.startswith("_"):
                continue
            try:
                attr = getattr(obj, name)
            except Exception:
                continue
            if not callable(attr):
                continue
            target = self.analyzer.find_repair_target(obj, name)
            repair_map[name] = target
            logger.debug("locate_repair_targets: %s → %s", name, target)

        return repair_map

    def emit_judgments(self) -> list[Judgment]:
        """Delegate judgment emission to the underlying analyser.

        Returns
        -------
        list[Judgment]
            Judgments produced from all chains analysed so far.
        """
        return self.analyzer.emit_judgments()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    # copilot: smoke test — verifies delegation chain detection and tracing
    print(f"[smoke] {__file__}")
    try:
        class Inner:
            def process(self, x):
                return x * 2

        class Outer:
            def __init__(self):
                self._impl = Inner()

            def __getattr__(self, name):
                return getattr(self._impl, name)

        detector = DelegationDetector()
        outer = Outer()
        links = detector.detect(outer)
        print(f"[smoke] detected {len(links)} delegation links")

        tracer = ChainTracer(detector=DelegationDetector())
        result = tracer.trace(outer)
        print(f"[smoke] chain: head={result.head} depth={result.depth} cycle={result.has_cycle}")

        locator = RepairTargetLocator()
        target = locator.locate(result, "process")
        print(f"[smoke] repair_target for 'process': {target!r}")

        analyzer = DelegationChainsAnalyzer(
            detector=DelegationDetector(),
            tracer=ChainTracer(detector=DelegationDetector()),
            locator=RepairTargetLocator(),
        )
        res = analyzer.analyze(outer)
        print(f"[smoke] analyzer chain_id={res.chain_id}")

        witness = DelegationChainsWitness()
        witness.register_expected_path("process", ["Outer", "Inner"])
        wr = witness.witness_dispatch("process", ["Outer", "Inner"])
        print(f"[smoke] witness unexpected_hops={wr.unexpected_hops}")

        coordinator = DelegationChainsCoordinator(analyzer=analyzer, witness=witness)
        coord = coordinator.coordinate(res)
        print(f"[smoke] coordinate components={coord.components}")
        print("[smoke] PASS")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[smoke] FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
