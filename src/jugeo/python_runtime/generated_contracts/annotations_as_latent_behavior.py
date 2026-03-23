"""
jugeo.python_runtime.generated_contracts.annotations_as_latent_behavior

theory2.tex Ch21 §21.1 — Annotations as Latent Behavioral Contracts.

Type annotations in Python are *latent* behavioral contracts: they exist in
__annotations__ and typing metadata but carry no runtime enforcement until
an external agent (type checker, runtime validator, contract enforcer) inspects
them.  At that moment the annotation is *promoted* from a latent section to an
active obligation in the JuGeo judgment system.

Sheaf semantics (§21.1.2): an annotation A on symbol S is a section
    σ_A ∈ Γ(U_S, F_contract)
where U_S is the open set covering S in the Python runtime site and F_contract
is the contract sheaf.  The section is latent when no restriction morphism
has been applied; it becomes active when a restriction
    ρ: U_S → U_checker
is composed with σ_A, yielding a residual obligation.

Promotion pathway (§21.1.3):
  latent → checked_by_copilot (ORACLE_PROPOSED, trust=2)
  checked_by_copilot → witnessed_at_runtime (RUNTIME_WITNESSED, trust=3)
  witnessed_at_runtime → solver_discharged (SOLVER_DISCHARGED, trust=4)
  solver_discharged → formally_proved (VERIFIED_PROOF, trust=5)

Exports: AnnotationsLatentBehaviorCoordinator, AnnotationsLatentBehaviorAnalyzer,
         AnnotationsLatentBehaviorWitness
"""

from __future__ import annotations

import abc
import enum
import functools
import inspect
import logging
import threading
import time
import typing
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo imports with inline stub fallbacks (theory2.tex §21.1.1)
# copilot: always wrap jugeo imports so this module is self-contained
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        CoordinateObject, CoordinateKind, CoordinateMorphism, MorphismKind,
        Site, SiteBuilder,
    )
except Exception:
    class CoordinateKind(enum.Enum):
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"
    class MorphismKind(enum.Enum):
        RESTRICTION = "restriction"; INCLUSION = "inclusion"
        TRANSPORT = "transport"; REFINEMENT = "refinement"
    @dataclass(frozen=True, slots=True)
    class CoordinateObject:
        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: dict = field(default_factory=dict)
    class CoordinateMorphism:
        def __init__(self, source, target, reason=""): self.source=source; self.target=target; self.reason=reason
    class Site: pass
    class SiteBuilder: pass

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, PropositionKind,
        Carrier, EvidenceBundle, EvidenceItem, EvidenceItemKind,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance, ProvenanceSource,
    )
except Exception:
    class TrustLevel(enum.IntEnum):
        CONTRADICTED=0; UNVERIFIED=1; ORACLE_PROPOSED=2
        RUNTIME_WITNESSED=3; SOLVER_DISCHARGED=4; VERIFIED_PROOF=5
    class JudgmentStatus(enum.Enum):
        PROPOSED="proposed"; CHALLENGED="challenged"; SETTLED="settled"; OBSTRUCTED="obstructed"
    class PropositionKind(enum.Enum):
        STRUCTURAL="structural"; BEHAVIORAL="behavioral"; RELATIONAL="relational"
        RESOURCE="resource"; SEMANTIC="semantic"
    class EvidenceItemKind(enum.Enum):
        SOLVER_PROOF="solver_proof"; RUNTIME_WITNESS="runtime_witness"
        ORACLE_PROPOSAL="oracle_proposal"; FORMAL_PROOF="formal_proof"
    class ProvenanceSource(enum.Enum):
        SOLVER="solver"; RUNTIME="runtime"; ORACLE="oracle"; HUMAN="human"; COMPOSED="composed"
    @dataclass(frozen=True, slots=True)
    class Proposition:
        kind: Any = None; formula: str = ""; free_variables: tuple[str,...] = ()
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class Carrier:
        name: str = ""; parameters: tuple[str,...] = (); is_dependent: bool = False
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class EvidenceItem:
        kind: Any = None; payload: dict = field(default_factory=dict); trust_level: Any = None
        channel: str = ""; timestamp: str = ""; expiry: str = ""; provenance: tuple[str,...] = ()
    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:
        items: tuple[Any,...] = ()
    @dataclass(frozen=True, slots=True)
    class ResidualObligation:
        description: str = ""; obligation_id: str = ""; priority: int = 1
        is_discharged: bool = False
        def discharge(self, evidence=""): return replace(self, is_discharged=True)
    @dataclass(frozen=True, slots=True)
    class Obstruction:
        description: str = ""; obstruction_id: str = ""; severity: int = 1
    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:
        level: Any = None; rationale: str = ""
    @dataclass(frozen=True, slots=True)
    class Provenance:
        sources: tuple[Any,...] = (); chain: tuple[str,...] = ()
    @dataclass(frozen=True, slots=True)
    class Judgment:
        coordinate: Any = None; proposition: Any = None; carrier: Any = None
        evidence: Any = None; obligations: tuple = (); obstructions: tuple = ()
        trust: Any = None; provenance: Any = None

try:
    from jugeo.python_runtime.generated_contracts.models import (
        AnnotationContract, ContractRecord, DecoratorTransformer, RegistrySection,
    )
except ImportError:
    @dataclass(frozen=True, slots=True)
    class AnnotationContract:
        symbol_name: str = ""; annotation_text: str = ""; trust_level: Any = None
        is_discharged: bool = False
    @dataclass(frozen=True, slots=True)
    class ContractRecord:
        coordinate_key: str = ""; contracts: tuple = (); is_complete: bool = False
    @dataclass(frozen=True, slots=True)
    class DecoratorTransformer:
        decorator_name: str = ""; source_qualname: str = ""; target_qualname: str = ""
        morphism_kind: str = "REFINEMENT"
    @dataclass(frozen=True, slots=True)
    class RegistrySection:
        registry_name: str = ""; entries: tuple = (); is_covering: bool = False


# ---------------------------------------------------------------------------
# Module-level constants (theory2.tex §21.1.0 — primitive type universe)
# copilot: these are the leaf types in the Python type lattice
# ---------------------------------------------------------------------------

_PRIMITIVE_TYPES: tuple[type, ...] = (int, float, str, bool, bytes, type(None))

_NUMERIC_TYPES: tuple[type, ...] = (int, float, complex)

_CONTAINER_TYPES: tuple[type, ...] = (list, tuple, dict, set, frozenset)

_ANNOTATION_PROMOTION_ORDER: tuple[int, ...] = (
    TrustLevel.UNVERIFIED,
    TrustLevel.ORACLE_PROPOSED,
    TrustLevel.RUNTIME_WITNESSED,
    TrustLevel.SOLVER_DISCHARGED,
    TrustLevel.VERIFIED_PROOF,
)

# copilot: maximum number of annotations we will inspect per object to avoid
# runaway introspection on very large classes
_MAX_ANNOTATIONS_PER_OBJECT: int = 512

# copilot: sentinel for annotations that could not be resolved
_UNRESOLVABLE_ANNOTATION: str = "<unresolvable>"

_MODULE_VERSION: str = "0.1.0"
_MODULE_NAME: str = "annotations_as_latent_behavior"


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str = "ann") -> str:
    """Generate a short unique identifier with an optional prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _is_annotated(annotation: Any) -> bool:
    """Return True when *annotation* is a typing.Annotated form.

    theory2.tex §21.1.4 — Annotated types carry extra metadata sections that
    can be promoted independently of the base type annotation.
    """
    origin = getattr(annotation, "__class__", None)
    return (
        hasattr(annotation, "__metadata__")
        and hasattr(annotation, "__origin__")
        and getattr(annotation, "__origin__", None) is not None
    )


def _annotation_to_str(annotation: Any) -> str:
    """Render *annotation* as a human-readable string, handling forward refs.

    We use repr() for most cases and fall back gracefully when the annotation
    is a string (PEP 563 deferred evaluation).
    """
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, type):
        return annotation.__qualname__
    try:
        return str(annotation)
    except Exception:
        return _UNRESOLVABLE_ANNOTATION


def _extract_base_type(annotation: Any) -> Any:
    """Extract the base type from a potentially wrapped annotation.

    For typing.Annotated[T, ...] this returns T.
    For typing.Optional[T] (Union[T, None]) this returns T.
    For plain types this is identity.
    """
    if _is_annotated(annotation):
        args = getattr(annotation, "__args__", ())
        if args:
            return args[0]
    origin = getattr(annotation, "__origin__", None)
    if origin is typing.Union:
        args = getattr(annotation, "__args__", ())
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _trust_level_name(level: Any) -> str:
    """Return the name of a TrustLevel as a string, tolerating None."""
    if level is None:
        return "NONE"
    try:
        return level.name
    except AttributeError:
        return str(level)


def _is_runtime_checkable(annotation: Any) -> bool:
    """Return True when *annotation* can be used in isinstance() checks.

    theory2.tex §21.1.5 — Only concrete types and runtime-checkable Protocols
    can serve as runtime witnesses without additional solver support.
    """
    if isinstance(annotation, type):
        return True
    # runtime_checkable protocols expose __protocol_attrs__
    if hasattr(annotation, "_is_runtime_protocol") and getattr(annotation, "_is_runtime_protocol", False):
        return True
    return False


def _violates(value: Any, annotation: Any) -> bool:
    """Return True when *value* violates *annotation* in a shallow check.

    We only perform isinstance checks; deeper structural checks require solver
    support (trust level SOLVER_DISCHARGED).
    """
    base = _extract_base_type(annotation)
    if not _is_runtime_checkable(base):
        return False  # cannot check at runtime — not a violation, just unknown
    if base is type(None):
        return value is not None
    try:
        return not isinstance(value, base)
    except TypeError:
        return False


# ---------------------------------------------------------------------------
# Core data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AnnotationRecord:
    """A single annotation lifted from a Python symbol into the JuGeo sheaf.

    theory2.tex §21.1.2 — each AnnotationRecord corresponds to a section
        σ_A ∈ Γ(U_S, F_contract)
    on the open neighbourhood U_S of symbol S in the runtime site.

    Fields
    ------
    symbol_name : str
        Qualified name of the annotated symbol (parameter, attribute, return).
    raw_annotation : str
        The annotation as it appears verbatim in source / __annotations__.
    resolved_annotation : str
        The annotation after forward-reference resolution via typing.get_type_hints().
    trust_level : TrustLevel
        Current trust level in the promotion ladder.
    is_checked : bool
        True when at least one checker has inspected this annotation.
    is_latent : bool
        True when no restriction morphism has been applied (still dormant).
    obligations : tuple[ResidualObligation, ...]
        Residual obligations generated by this annotation.
    metadata : dict
        Arbitrary metadata attached during inspection.
    """

    symbol_name: str = ""
    raw_annotation: str = ""
    resolved_annotation: str = ""
    trust_level: Any = None
    is_checked: bool = False
    is_latent: bool = True
    obligations: tuple = ()
    metadata: dict = field(default_factory=dict)

    def promote(self, new_trust_level: Any) -> AnnotationRecord:
        """Return a new AnnotationRecord promoted to *new_trust_level*.

        theory2.tex §21.1.3 — promotion is a restriction morphism application.
        Promotion is monotone: we never lower trust.

        Parameters
        ----------
        new_trust_level : TrustLevel
            Target trust level; must be ≥ current level.

        Returns
        -------
        AnnotationRecord
            New frozen instance with updated trust_level, is_latent=False,
            is_checked=True.
        """
        current = self.trust_level
        if current is not None:
            try:
                if int(new_trust_level) < int(current):
                    logger.warning(
                        "promote: attempted trust demotion on %s (%s → %s); skipping",
                        self.symbol_name, current, new_trust_level,
                    )
                    return self
            except (TypeError, ValueError):
                pass
        logger.debug("promote: %s %s → %s", self.symbol_name, current, new_trust_level)
        return replace(self, trust_level=new_trust_level, is_latent=False, is_checked=True)

    def add_obligation(self, ob: Any) -> AnnotationRecord:
        """Return a new AnnotationRecord with *ob* appended to obligations.

        theory2.tex §21.1.6 — obligations are generated by the restriction
        morphism and accumulate over the promotion ladder.
        """
        return replace(self, obligations=self.obligations + (ob,))

    def summary(self) -> str:
        """Return a human-readable one-line summary of this annotation record.

        Includes: symbol name, raw annotation, trust level, latent flag, and
        count of residual obligations.
        """
        latent_tag = "LATENT" if self.is_latent else "ACTIVE"
        trust_name = _trust_level_name(self.trust_level)
        n_obs = len(self.obligations)
        return (
            f"AnnotationRecord({self.symbol_name!r}: {self.raw_annotation!r}"
            f" | trust={trust_name} | {latent_tag} | obligations={n_obs})"
        )

    def to_dict(self) -> dict:
        """Serialize this record to a plain dictionary.

        Suitable for JSON encoding, logging, and inter-process transport.
        All non-serializable values are converted to their string representation.
        """
        return {
            "symbol_name": self.symbol_name,
            "raw_annotation": self.raw_annotation,
            "resolved_annotation": self.resolved_annotation,
            "trust_level": _trust_level_name(self.trust_level),
            "is_checked": self.is_checked,
            "is_latent": self.is_latent,
            "obligations_count": len(self.obligations),
            "obligations": [
                getattr(ob, "description", str(ob)) for ob in self.obligations
            ],
            "metadata": {k: str(v) for k, v in self.metadata.items()},
        }


@dataclass(frozen=True, slots=True)
class WitnessRecord:
    """Record of a single witnessed function call and its annotation violations.

    theory2.tex §21.1.7 — a witness record is a section of the evidence sheaf
    restricted to a single call event U_call ⊆ U_S.

    Fields
    ------
    func_qualname : str
        Qualified name of the function that was called.
    call_id : str
        Unique identifier for this call event.
    arg_violations : tuple[str, ...]
        Positional argument violations as human-readable descriptions.
    kwarg_violations : tuple[str, ...]
        Keyword argument violations.
    return_violation : str
        Return-value violation, or "" if the return was compliant.
    timestamp : str
        ISO-8601 timestamp of the call.
    trust_level : TrustLevel
        Trust level assigned to this witness.
    """

    func_qualname: str = ""
    call_id: str = ""
    arg_violations: tuple = ()
    kwarg_violations: tuple = ()
    return_violation: str = ""
    timestamp: str = ""
    trust_level: Any = None

    def is_clean(self) -> bool:
        """Return True when no violations were observed."""
        return (
            len(self.arg_violations) == 0
            and len(self.kwarg_violations) == 0
            and self.return_violation == ""
        )

    def total_violations(self) -> int:
        """Return total count of all violations observed in this call."""
        return len(self.arg_violations) + len(self.kwarg_violations) + (
            1 if self.return_violation else 0
        )

    def summary(self) -> str:
        """One-line summary of this witness record."""
        status = "CLEAN" if self.is_clean() else f"VIOLATED({self.total_violations()})"
        return (
            f"WitnessRecord(func={self.func_qualname!r}"
            f" id={self.call_id} {status} @{self.timestamp})"
        )


# ---------------------------------------------------------------------------
# AnnotationInspector
# ---------------------------------------------------------------------------

class AnnotationInspector:
    """Inspect Python objects to extract AnnotationRecord instances.

    theory2.tex §21.1.8 — the inspector is the restriction functor
        ρ*: Ob(Site) → Sections(F_contract)
    applied to a Python object, yielding the set of its latent annotation
    sections before any promotion has occurred.

    The inspector handles:
    - Functions and methods (parameter + return annotations)
    - Classes (class-level attributes, method parameters)
    - Modules (all top-level annotated names)
    - typing.Annotated forms (metadata extraction)
    - Forward references (deferred resolution via get_type_hints)
    """

    def __init__(self) -> None:
        # copilot: cache resolved hints to avoid repeated get_type_hints calls
        self._hints_cache: dict[int, dict] = {}
        self._inspection_count: int = 0

    def inspect_object(self, obj: Any) -> list[AnnotationRecord]:
        """Inspect *obj* and return a list of AnnotationRecord instances.

        Handles functions, classes, and arbitrary objects with __annotations__.
        Each annotation becomes a separate AnnotationRecord in the LATENT state
        with trust_level=UNVERIFIED.

        Parameters
        ----------
        obj : Any
            The Python object to inspect.  Must have __annotations__ or be
            introspectable via typing.get_type_hints().

        Returns
        -------
        list[AnnotationRecord]
            One record per discovered annotation, capped at
            _MAX_ANNOTATIONS_PER_OBJECT.
        """
        self._inspection_count += 1
        records: list[AnnotationRecord] = []

        if inspect.isfunction(obj) or inspect.ismethod(obj):
            records.extend(self._inspect_callable(obj))
        elif inspect.isclass(obj):
            records.extend(self._inspect_class(obj))
        elif inspect.ismodule(obj):
            records.extend(self.inspect_module(obj))
        else:
            # copilot: try generic __annotations__ introspection for instances
            records.extend(self._inspect_generic(obj))

        # cap to avoid memory issues with very large classes
        if len(records) > _MAX_ANNOTATIONS_PER_OBJECT:
            logger.warning(
                "inspect_object: truncating %d records to %d for %s",
                len(records), _MAX_ANNOTATIONS_PER_OBJECT, getattr(obj, "__qualname__", repr(obj)),
            )
            records = records[:_MAX_ANNOTATIONS_PER_OBJECT]

        logger.debug("inspect_object: found %d annotations on %s", len(records), obj)
        return records

    def _inspect_callable(self, func: Any) -> list[AnnotationRecord]:
        """Extract parameter and return annotations from a callable.

        Uses inspect.signature for parameter names and typing.get_type_hints
        for resolved types, falling back to __annotations__ when hints fail.
        """
        records: list[AnnotationRecord] = []
        qualname = getattr(func, "__qualname__", repr(func))
        raw_anns = getattr(func, "__annotations__", {})
        resolved_hints = self._safe_get_hints(func)

        try:
            sig = inspect.signature(func)
        except (ValueError, TypeError):
            sig = None

        for param_name, raw_ann in raw_anns.items():
            raw_str = _annotation_to_str(raw_ann)
            resolved_str = _annotation_to_str(resolved_hints.get(param_name, raw_ann))
            annotated_meta = self.extract_annotated_metadata(resolved_hints.get(param_name, raw_ann))
            symbol = f"{qualname}:{param_name}"
            metadata: dict = {}
            if annotated_meta:
                metadata["annotated_metadata"] = [str(m) for m in annotated_meta]
            if sig is not None and param_name in sig.parameters:
                p = sig.parameters[param_name]
                metadata["has_default"] = p.default is not inspect.Parameter.empty
                metadata["kind"] = p.kind.name
            rec = AnnotationRecord(
                symbol_name=symbol,
                raw_annotation=raw_str,
                resolved_annotation=resolved_str,
                trust_level=TrustLevel.UNVERIFIED,
                is_checked=False,
                is_latent=True,
                obligations=(),
                metadata=metadata,
            )
            records.append(rec)
            logger.debug("_inspect_callable: %s → %s", symbol, raw_str)

        return records

    def _inspect_class(self, cls: type) -> list[AnnotationRecord]:
        """Extract annotations from a class (class body + all methods).

        Inspects class-level __annotations__ first, then iterates over all
        methods defined directly on the class (not inherited).
        """
        records: list[AnnotationRecord] = []
        qualname = getattr(cls, "__qualname__", repr(cls))

        # class-level attribute annotations
        class_anns = getattr(cls, "__annotations__", {})
        resolved = self._safe_get_hints(cls)
        for attr_name, raw_ann in class_anns.items():
            raw_str = _annotation_to_str(raw_ann)
            resolved_str = _annotation_to_str(resolved.get(attr_name, raw_ann))
            symbol = f"{qualname}.{attr_name}"
            annotated_meta = self.extract_annotated_metadata(resolved.get(attr_name, raw_ann))
            metadata: dict = {"class_attribute": True}
            if annotated_meta:
                metadata["annotated_metadata"] = [str(m) for m in annotated_meta]
            records.append(AnnotationRecord(
                symbol_name=symbol,
                raw_annotation=raw_str,
                resolved_annotation=resolved_str,
                trust_level=TrustLevel.UNVERIFIED,
                is_checked=False,
                is_latent=True,
                obligations=(),
                metadata=metadata,
            ))

        # method annotations (only own methods, not inherited)
        for name, member in inspect.getmembers(cls, predicate=inspect.isfunction):
            if name.startswith("__") and name.endswith("__") and name not in ("__init__", "__call__"):
                continue
            if member.__qualname__.startswith(qualname):
                records.extend(self._inspect_callable(member))

        return records

    def inspect_module(self, module: Any) -> list[AnnotationRecord]:
        """Inspect all classes and functions in *module*.

        theory2.tex §21.1.9 — module-level inspection scans the entire
        namespace, yielding annotations from all public symbols.
        """
        records: list[AnnotationRecord] = []
        for name in dir(module):
            if name.startswith("_"):
                continue
            try:
                obj = getattr(module, name)
            except AttributeError:
                continue
            if inspect.isfunction(obj):
                records.extend(self._inspect_callable(obj))
            elif inspect.isclass(obj):
                records.extend(self._inspect_class(obj))
        logger.debug("inspect_module: %d records from %s", len(records), module)
        return records

    def _inspect_generic(self, obj: Any) -> list[AnnotationRecord]:
        """Fallback inspection for arbitrary objects via __annotations__."""
        records: list[AnnotationRecord] = []
        anns = getattr(obj, "__annotations__", {})
        qualname = getattr(obj, "__qualname__", repr(obj))
        for attr_name, raw_ann in anns.items():
            raw_str = _annotation_to_str(raw_ann)
            records.append(AnnotationRecord(
                symbol_name=f"{qualname}.{attr_name}",
                raw_annotation=raw_str,
                resolved_annotation=raw_str,
                trust_level=TrustLevel.UNVERIFIED,
                is_checked=False,
                is_latent=True,
                obligations=(),
                metadata={"generic_fallback": True},
            ))
        return records

    def extract_annotated_metadata(self, annotation: Any) -> list[Any]:
        """Extract the extra args from a typing.Annotated[T, *metadata] form.

        theory2.tex §21.1.4 — the metadata args are *explicit contract hints*
        embedded directly in the annotation.  Each metadata item is a candidate
        section of F_contract that can be promoted independently.

        Parameters
        ----------
        annotation : Any
            The annotation to inspect.  If it is not an Annotated form, returns [].

        Returns
        -------
        list[Any]
            The metadata arguments (everything after the base type).
        """
        if not _is_annotated(annotation):
            return []
        metadata = getattr(annotation, "__metadata__", ())
        return list(metadata)

    def _safe_get_hints(self, obj: Any) -> dict:
        """Call typing.get_type_hints(obj, include_extras=True) safely.

        Returns an empty dict when hints cannot be resolved (e.g., forward
        references that are not yet defined, NameError in eval context).
        Caches results keyed by id(obj).
        """
        obj_id = id(obj)
        if obj_id in self._hints_cache:
            return self._hints_cache[obj_id]
        hints: dict = {}
        try:
            hints = typing.get_type_hints(obj, include_extras=True)
        except Exception as exc:
            logger.debug("_safe_get_hints: failed for %s: %s", obj, exc)
        self._hints_cache[obj_id] = hints
        return hints


# ---------------------------------------------------------------------------
# LatencyPromotionEngine
# ---------------------------------------------------------------------------

class LatencyPromotionEngine:
    """Engine that drives annotations up the promotion ladder.

    theory2.tex §21.1.3 — the promotion engine is the functor
        Promote: Sections_latent → Sections_active
    that applies restriction morphisms to latent annotation sections,
    yielding active obligations at higher trust levels.

    The engine records a full history of all promotions so that audit trails
    can be reconstructed and presented to the judgment system.
    """

    def __init__(self) -> None:
        # copilot: history entries are (before, after) pairs for traceability
        self._history: list[tuple[AnnotationRecord, AnnotationRecord]] = []
        self._promotion_count: int = 0
        self._failed_promotions: int = 0

    def promote(
        self,
        record: AnnotationRecord,
        new_trust: Any,
        evidence: str = "",
    ) -> AnnotationRecord:
        """Promote *record* to *new_trust* and record in history.

        theory2.tex §21.1.3 — each promotion step corresponds to applying a
        restriction morphism ρ to the latent section σ_A.

        Parameters
        ----------
        record : AnnotationRecord
            The annotation to promote.
        new_trust : TrustLevel
            Target trust level.
        evidence : str
            Human-readable description of the evidence that justifies the
            promotion (used for audit trails).

        Returns
        -------
        AnnotationRecord
            The promoted record (or the original if promotion fails).
        """
        try:
            promoted = record.promote(new_trust)
            self._history.append((record, promoted))
            self._promotion_count += 1
            logger.debug(
                "LatencyPromotionEngine.promote: %s → trust=%s (evidence=%r)",
                record.symbol_name, _trust_level_name(new_trust), evidence,
            )
            return promoted
        except Exception as exc:
            self._failed_promotions += 1
            logger.warning("LatencyPromotionEngine.promote: failed for %s: %s", record.symbol_name, exc)
            return record

    def promote_all(
        self,
        records: list[AnnotationRecord],
        evidence_map: dict,
    ) -> list[AnnotationRecord]:
        """Promote each record in *records* if its symbol has an entry in *evidence_map*.

        theory2.tex §21.1.10 — bulk promotion corresponds to a natural
        transformation applied to all sections simultaneously.

        Parameters
        ----------
        records : list[AnnotationRecord]
            Annotations to potentially promote.
        evidence_map : dict
            Maps symbol_name → (trust_level, evidence_str).  Only symbols
            present in this map are promoted.

        Returns
        -------
        list[AnnotationRecord]
            New list with promoted records substituted in place.
        """
        result: list[AnnotationRecord] = []
        for rec in records:
            if rec.symbol_name in evidence_map:
                trust, evidence = evidence_map[rec.symbol_name]
                result.append(self.promote(rec, trust, evidence))
            else:
                result.append(rec)
        return result

    def compute_promotion_fraction(self, records: list[AnnotationRecord]) -> float:
        """Compute the fraction of *records* that have been promoted from latent.

        Returns a float in [0.0, 1.0].  An empty list returns 0.0.
        """
        if not records:
            return 0.0
        promoted = sum(1 for r in records if not r.is_latent)
        return promoted / len(records)

    def promotion_report(self) -> str:
        """Generate a multi-line summary of all promotions performed.

        Includes totals, per-symbol details, and failure counts.
        """
        lines: list[str] = [
            f"LatencyPromotionEngine Report",
            f"  Total promotions : {self._promotion_count}",
            f"  Failed promotions: {self._failed_promotions}",
            f"  History entries  : {len(self._history)}",
            "",
        ]
        for before, after in self._history:
            before_trust = _trust_level_name(before.trust_level)
            after_trust = _trust_level_name(after.trust_level)
            lines.append(
                f"  {before.symbol_name!r}: {before_trust} → {after_trust}"
            )
        return "\n".join(lines)

    def get_history(self) -> list[tuple[AnnotationRecord, AnnotationRecord]]:
        """Return a copy of the full promotion history."""
        return list(self._history)

    def reset(self) -> None:
        """Clear all history and reset counters."""
        self._history.clear()
        self._promotion_count = 0
        self._failed_promotions = 0


# ---------------------------------------------------------------------------
# AnnotationsLatentBehaviorAnalyzer
# ---------------------------------------------------------------------------

class AnnotationsLatentBehaviorAnalyzer:
    """High-level analyzer that inspects objects and produces judgments.

    theory2.tex §21.1.11 — the analyzer implements the composition
        J = Promote ∘ Inspect: Ob(Site) → Judgments(F_contract)
    which maps each Python object to a set of JuGeo judgments representing
    the behavioral contracts embedded in its annotations.

    Usage
    -----
    >>> analyzer = AnnotationsLatentBehaviorAnalyzer()
    >>> records = analyzer.analyze(my_function)
    >>> judgments = analyzer.emit_judgments(records)
    """

    def __init__(self) -> None:
        # copilot: inspector extracts raw sections; engine promotes them
        self._inspector = AnnotationInspector()
        self._engine = LatencyPromotionEngine()
        self._last_records: list[AnnotationRecord] = []
        self._judgment_count: int = 0

    def analyze(self, obj: Any) -> list[AnnotationRecord]:
        """Inspect *obj* and auto-promote annotations to RUNTIME_WITNESSED.

        Auto-promotion occurs when the object's __annotations__ dict is
        non-empty, indicating that a runtime agent has already observed the
        annotations (theory2.tex §21.1.12).

        Parameters
        ----------
        obj : Any
            The Python object to analyze.

        Returns
        -------
        list[AnnotationRecord]
            Promoted annotation records.
        """
        records = self._inspector.inspect_object(obj)

        # auto-promote if __annotations__ is non-empty
        raw_anns = getattr(obj, "__annotations__", {})
        if raw_anns:
            evidence_map = {
                rec.symbol_name: (TrustLevel.RUNTIME_WITNESSED, "runtime_annotations_dict")
                for rec in records
            }
            records = self._engine.promote_all(records, evidence_map)

        self._last_records = records
        logger.debug("analyze: %d records for %s", len(records), obj)
        return records

    def emit_judgments(
        self, records: list[AnnotationRecord] | None = None
    ) -> list[Judgment]:
        """Convert annotation records into JuGeo Judgment objects.

        theory2.tex §21.1.13 — each annotation section becomes a Judgment
        with:
        - A Proposition asserting the annotation's behavioral contract
        - A Carrier naming the annotated symbol
        - An EvidenceBundle derived from the promotion history
        - A TrustAnnotation reflecting the current trust level
        - A Provenance tracing the inspection + promotion path

        Parameters
        ----------
        records : list[AnnotationRecord] | None
            Records to convert.  Defaults to the last analyze() result.

        Returns
        -------
        list[Judgment]
            One Judgment per AnnotationRecord.
        """
        if records is None:
            records = self._last_records

        judgments: list[Judgment] = []
        for rec in records:
            proposition = Proposition(
                kind=PropositionKind.BEHAVIORAL,
                formula=f"annotation({rec.symbol_name!r}) : {rec.resolved_annotation}",
                free_variables=(rec.symbol_name,),
                metadata={
                    "raw": rec.raw_annotation,
                    "resolved": rec.resolved_annotation,
                },
            )
            carrier = Carrier(
                name=rec.symbol_name,
                parameters=(),
                is_dependent=False,
                metadata={"latent": rec.is_latent},
            )
            evidence_item = EvidenceItem(
                kind=EvidenceItemKind.RUNTIME_WITNESS if not rec.is_latent else EvidenceItemKind.ORACLE_PROPOSAL,
                payload={"annotation": rec.raw_annotation},
                trust_level=rec.trust_level,
                channel="annotation_inspector",
                timestamp=_now_iso(),
                expiry="",
                provenance=("annotation_inspector", "latency_promotion_engine"),
            )
            bundle = EvidenceBundle(items=(evidence_item,))
            trust_ann = TrustAnnotation(
                level=rec.trust_level,
                rationale=f"auto-promoted from annotation inspection on {rec.symbol_name}",
            )
            prov = Provenance(
                sources=(ProvenanceSource.RUNTIME,),
                chain=("AnnotationInspector", "LatencyPromotionEngine", "AnnotationsLatentBehaviorAnalyzer"),
            )
            judgment = Judgment(
                coordinate=None,
                proposition=proposition,
                carrier=carrier,
                evidence=bundle,
                obligations=rec.obligations,
                obstructions=(),
                trust=trust_ann,
                provenance=prov,
            )
            judgments.append(judgment)
            self._judgment_count += 1

        logger.debug("emit_judgments: produced %d judgments", len(judgments))
        return judgments

    def summary(self) -> str:
        """Return a multi-line summary of the last analysis run."""
        records = self._last_records
        total = len(records)
        latent = sum(1 for r in records if r.is_latent)
        active = total - latent
        fraction = self._engine.compute_promotion_fraction(records)
        lines = [
            f"AnnotationsLatentBehaviorAnalyzer Summary",
            f"  Total annotations : {total}",
            f"  Latent            : {latent}",
            f"  Active (promoted) : {active}",
            f"  Promotion fraction: {fraction:.2%}",
            f"  Judgments emitted : {self._judgment_count}",
            "",
            self._engine.promotion_report(),
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# AnnotationsLatentBehaviorWitness
# ---------------------------------------------------------------------------

class AnnotationsLatentBehaviorWitness:
    """Runtime witness that observes function calls and checks annotation compliance.

    theory2.tex §21.1.7 — the witness implements a *runtime restriction*:
    for each call event e ∈ U_S, the witness restricts the annotation section
    σ_A to the event neighbourhood U_e, yielding a local section (or
    obstruction) that becomes evidence for (or against) the contract.

    The witness performs shallow isinstance() checks; deep structural validation
    requires solver support (trust level ≥ SOLVER_DISCHARGED).
    """

    def __init__(self) -> None:
        # copilot: store violations for later aggregation
        self._violations: list[WitnessRecord] = []
        self._call_count: int = 0
        self._clean_calls: int = 0
        self._lock = threading.Lock()

    def witness_call(
        self,
        func: Any,
        args: tuple,
        kwargs: dict,
    ) -> WitnessRecord:
        """Observe a call to *func* with *args* and *kwargs*.

        Checks each positional and keyword argument against its annotation.
        Returns a WitnessRecord with all observed violations.

        Parameters
        ----------
        func : Any
            The callable being monitored.
        args : tuple
            Positional arguments passed to the call.
        kwargs : dict
            Keyword arguments passed to the call.

        Returns
        -------
        WitnessRecord
            Immutable record of the observed call.
        """
        self._call_count += 1
        qualname = getattr(func, "__qualname__", repr(func))
        call_id = _new_id("call")
        hints = {}
        try:
            hints = typing.get_type_hints(func, include_extras=True)
        except Exception:
            pass

        arg_violations: list[str] = []
        kwarg_violations: list[str] = []
        return_violation: str = ""

        # check positional arguments against signature
        try:
            sig = inspect.signature(func)
            params = list(sig.parameters.values())
            for i, arg_val in enumerate(args):
                if i < len(params):
                    pname = params[i].name
                    ann = hints.get(pname)
                    if ann is not None:
                        viol = self._check_arg(arg_val, ann, pname)
                        if viol:
                            arg_violations.append(viol)
        except (ValueError, TypeError) as exc:
            logger.debug("witness_call: signature inspection failed: %s", exc)

        # check keyword arguments
        for kname, kval in kwargs.items():
            ann = hints.get(kname)
            if ann is not None:
                viol = self._check_arg(kval, ann, kname)
                if viol:
                    kwarg_violations.append(viol)

        record = WitnessRecord(
            func_qualname=qualname,
            call_id=call_id,
            arg_violations=tuple(arg_violations),
            kwarg_violations=tuple(kwarg_violations),
            return_violation=return_violation,
            timestamp=_now_iso(),
            trust_level=TrustLevel.RUNTIME_WITNESSED,
        )

        with self._lock:
            self._violations.append(record)
            if record.is_clean():
                self._clean_calls += 1

        logger.debug(
            "witness_call: %s %s violations=%d",
            qualname, call_id, record.total_violations(),
        )
        return record

    def _check_arg(self, value: Any, annotation: Any, name: str) -> str:
        """Check *value* against *annotation* for parameter *name*.

        Returns an empty string when the value satisfies the annotation.
        Returns a violation description string when it does not.

        Only performs shallow isinstance() checks (theory2.tex §21.1.5).
        Returns "" when the annotation is not runtime-checkable.
        """
        base = _extract_base_type(annotation)
        if not _is_runtime_checkable(base):
            return ""
        if base is type(None):
            if value is not None:
                return f"{name}: expected None, got {type(value).__name__}"
            return ""
        try:
            if not isinstance(value, base):
                return (
                    f"{name}: expected {getattr(base, '__name__', str(base))!r}"
                    f", got {type(value).__name__!r}"
                    f" (value={value!r})"
                )
        except TypeError as exc:
            logger.debug("_check_arg: isinstance failed for %s: %s", name, exc)
        return ""

    def get_violations(self) -> list[WitnessRecord]:
        """Return a copy of the recorded violations list."""
        with self._lock:
            return list(self._violations)

    def get_violations_for(self, func_qualname: str) -> list[WitnessRecord]:
        """Return violations for a specific function qualified name."""
        with self._lock:
            return [v for v in self._violations if v.func_qualname == func_qualname]

    def clear(self) -> None:
        """Clear all recorded violations and reset counters."""
        with self._lock:
            self._violations.clear()
            self._call_count = 0
            self._clean_calls = 0

    def summary(self) -> str:
        """Return a multi-line summary of witness activity."""
        with self._lock:
            total_viols = sum(v.total_violations() for v in self._violations)
            dirty_calls = self._call_count - self._clean_calls
            return (
                f"AnnotationsLatentBehaviorWitness Summary\n"
                f"  Total calls       : {self._call_count}\n"
                f"  Clean calls       : {self._clean_calls}\n"
                f"  Dirty calls       : {dirty_calls}\n"
                f"  Total violations  : {total_viols}\n"
            )


# ---------------------------------------------------------------------------
# AnnotationsLatentBehaviorCoordinator
# ---------------------------------------------------------------------------

class AnnotationsLatentBehaviorCoordinator:
    """Top-level coordinator for annotation-as-latent-behavior analysis.

    theory2.tex §21.1.14 — the coordinator implements the full pipeline:
        Ob(Site) → AnnotationRecords → Judgments → Witness coverage

    It is thread-safe and can be shared across threads.

    Usage
    -----
    >>> coordinator = AnnotationsLatentBehaviorCoordinator()
    >>> result = coordinator.run_analysis(my_func)
    >>> wrapped = coordinator.install_witness(my_func)
    >>> wrapped(1, "hello")  # observed call
    >>> print(coordinator.report())
    """

    def __init__(self) -> None:
        # copilot: analyzer and witness are created once and reused
        self._analyzer = AnnotationsLatentBehaviorAnalyzer()
        self._witness = AnnotationsLatentBehaviorWitness()
        self._lock = threading.Lock()
        self._analyzed_objects: list[str] = []
        self._coordinator_id: str = _new_id("coord")

    def coordinate(self, obj: Any) -> CoordinateObject:
        """Build a CoordinateObject from *obj*'s qualified name and module.

        theory2.tex §21.1.15 — the coordinate situates the object in the
        Python runtime site, providing the topological address for its sections.
        """
        qualname = getattr(obj, "__qualname__", repr(obj))
        module_name = getattr(obj, "__module__", "<unknown>")
        components = tuple(
            part for part in f"{module_name}.{qualname}".split(".") if part
        )
        kind = CoordinateKind.FUNCTION if callable(obj) else CoordinateKind.MODULE
        return CoordinateObject(
            components=components,
            kind=kind,
            support_labels=frozenset({module_name}),
            metadata={"coordinator_id": self._coordinator_id},
        )

    def run_analysis(self, obj: Any) -> dict:
        """Run the full annotation analysis pipeline on *obj*.

        Thread-safe. Returns a dict with keys:
        - annotations  : list[AnnotationRecord]
        - judgments    : list[Judgment]
        - obligations  : list[ResidualObligation]
        - obstructions : list[Obstruction]
        - summary      : str

        Parameters
        ----------
        obj : Any
            The Python object to analyze.

        Returns
        -------
        dict
            Analysis results.
        """
        with self._lock:
            qualname = getattr(obj, "__qualname__", repr(obj))
            logger.info("run_analysis: starting on %s", qualname)
            self._analyzed_objects.append(qualname)

            records = self._analyzer.analyze(obj)
            judgments = self._analyzer.emit_judgments(records)

            # collect all obligations from records
            obligations: list = []
            for rec in records:
                obligations.extend(rec.obligations)

            # identify obstructions: latent annotations that could not be promoted
            obstructions: list = []
            for rec in records:
                if rec.is_latent and rec.resolved_annotation == _UNRESOLVABLE_ANNOTATION:
                    obstructions.append(Obstruction(
                        description=f"Unresolvable annotation on {rec.symbol_name}",
                        obstruction_id=_new_id("obs"),
                        severity=1,
                    ))

            result = {
                "annotations": records,
                "judgments": judgments,
                "obligations": obligations,
                "obstructions": obstructions,
                "summary": self._analyzer.summary(),
            }
            logger.info(
                "run_analysis: done on %s — %d annotations, %d judgments",
                qualname, len(records), len(judgments),
            )
            return result

    def install_witness(self, func: Any) -> Any:
        """Wrap *func* with the witness so all calls are observed.

        Returns a new callable that, when called, first passes through the
        witness and then delegates to the original function.

        Parameters
        ----------
        func : Any
            The function to wrap.

        Returns
        -------
        callable
            Wrapped function.
        """
        witness = self._witness

        @functools.wraps(func)
        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            # copilot: observe the call before dispatching
            witness.witness_call(func, args, kwargs)
            return func(*args, **kwargs)

        logger.debug("install_witness: wrapped %s", getattr(func, "__qualname__", func))
        return _wrapped

    def report(self) -> str:
        """Return a comprehensive multi-line report of all coordinator activity."""
        lines = [
            f"AnnotationsLatentBehaviorCoordinator Report",
            f"  Coordinator ID      : {self._coordinator_id}",
            f"  Analyzed objects    : {len(self._analyzed_objects)}",
            "",
            "  Analyzed symbols:",
        ]
        for sym in self._analyzed_objects:
            lines.append(f"    - {sym}")
        lines.append("")
        lines.append(self._analyzer.summary())
        lines.append(self._witness.summary())
        return "\n".join(lines)

    def get_witness(self) -> AnnotationsLatentBehaviorWitness:
        """Return the internal witness for external inspection."""
        return self._witness

    def get_analyzer(self) -> AnnotationsLatentBehaviorAnalyzer:
        """Return the internal analyzer for external inspection."""
        return self._analyzer


# ---------------------------------------------------------------------------
# Additional helpers and utilities
# ---------------------------------------------------------------------------

def build_annotation_summary_table(records: list[AnnotationRecord]) -> str:
    """Build a formatted ASCII table summarising a list of AnnotationRecord.

    Each row contains symbol name, raw annotation, trust level, and latent flag.
    Used for diagnostic output and logging (theory2.tex §21.1.16).
    """
    if not records:
        return "(no annotation records)"

    col_widths = [40, 30, 20, 8]
    headers = ["Symbol", "Annotation", "Trust", "Latent"]

    def row(cols: list[str]) -> str:
        padded = [c[:col_widths[i]].ljust(col_widths[i]) for i, c in enumerate(cols)]
        return "| " + " | ".join(padded) + " |"

    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    lines = [sep, row(headers), sep]
    for rec in records:
        trust_str = _trust_level_name(rec.trust_level)
        latent_str = "yes" if rec.is_latent else "no"
        lines.append(row([rec.symbol_name, rec.raw_annotation, trust_str, latent_str]))
    lines.append(sep)
    return "\n".join(lines)


def compute_annotation_coverage(records: list[AnnotationRecord]) -> dict:
    """Compute coverage statistics for a list of AnnotationRecord.

    Returns a dict with keys: total, latent, active, fraction_active,
    by_trust (dict mapping trust name → count), has_violations (bool).
    """
    total = len(records)
    latent = sum(1 for r in records if r.is_latent)
    active = total - latent
    by_trust: dict = {}
    for rec in records:
        tname = _trust_level_name(rec.trust_level)
        by_trust[tname] = by_trust.get(tname, 0) + 1
    has_violations = any(len(r.obligations) > 0 for r in records)
    return {
        "total": total,
        "latent": latent,
        "active": active,
        "fraction_active": active / total if total else 0.0,
        "by_trust": by_trust,
        "has_violations": has_violations,
    }


def filter_records_by_trust(
    records: list[AnnotationRecord],
    min_trust: Any,
) -> list[AnnotationRecord]:
    """Return only those records whose trust level is >= *min_trust*.

    Useful for selecting records that meet a minimum evidence threshold before
    submitting them to the solver (theory2.tex §21.1.17).
    """
    result: list[AnnotationRecord] = []
    for rec in records:
        if rec.trust_level is None:
            continue
        try:
            if int(rec.trust_level) >= int(min_trust):
                result.append(rec)
        except (TypeError, ValueError):
            pass
    return result


def merge_annotation_records(
    records_a: list[AnnotationRecord],
    records_b: list[AnnotationRecord],
) -> list[AnnotationRecord]:
    """Merge two lists of AnnotationRecord, de-duplicating by symbol_name.

    When both lists contain a record for the same symbol, the one with the
    higher trust level wins (monotone merge — theory2.tex §21.1.18).
    """
    merged: dict[str, AnnotationRecord] = {}
    for rec in records_a:
        merged[rec.symbol_name] = rec
    for rec in records_b:
        existing = merged.get(rec.symbol_name)
        if existing is None:
            merged[rec.symbol_name] = rec
        else:
            # copilot: monotone merge — keep the higher trust level
            try:
                if rec.trust_level is not None and (
                    existing.trust_level is None or int(rec.trust_level) > int(existing.trust_level)
                ):
                    merged[rec.symbol_name] = rec
            except (TypeError, ValueError):
                merged[rec.symbol_name] = rec
    return list(merged.values())


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    print(f"[smoke] {__file__}")
    try:
        coordinator = AnnotationsLatentBehaviorCoordinator()

        def example_func(x: int, y: str = "hello") -> bool:
            """A simple annotated function for smoke testing."""
            return True

        result = coordinator.run_analysis(example_func)
        assert "annotations" in result, "missing 'annotations' key"
        assert "judgments" in result, "missing 'judgments' key"
        assert "obligations" in result, "missing 'obligations' key"
        assert "obstructions" in result, "missing 'obstructions' key"
        assert "summary" in result, "missing 'summary' key"

        witness = AnnotationsLatentBehaviorWitness()
        wr = witness.witness_call(example_func, (42, "world"), {})
        assert isinstance(wr, WitnessRecord), f"expected WitnessRecord, got {type(wr)}"
        assert wr.is_clean(), f"unexpected violations: {wr.arg_violations}"

        wr_bad = witness.witness_call(example_func, ("not-an-int", "world"), {})
        assert not wr_bad.is_clean(), "expected violation for bad int arg"

        analyzer = AnnotationsLatentBehaviorAnalyzer()
        records = analyzer.analyze(example_func)
        judgments = analyzer.emit_judgments(records)
        assert len(records) > 0, "expected at least one annotation record"
        assert len(judgments) > 0, "expected at least one judgment"
        print(f"[smoke] records={len(records)}, judgments={len(judgments)}")

        # test coverage utilities
        coverage = compute_annotation_coverage(records)
        assert "fraction_active" in coverage

        table = build_annotation_summary_table(records)
        assert "Symbol" in table

        wrapped = coordinator.install_witness(example_func)
        assert wrapped(1, "test") is True

        print(coordinator.report())
        print("[smoke] PASS")
    except Exception as exc:
        import traceback; traceback.print_exc()
        print(f"[smoke] FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
