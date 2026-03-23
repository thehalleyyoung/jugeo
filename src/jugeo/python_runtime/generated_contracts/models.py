r"""jugeo.python_runtime.generated_contracts.models — Core data models for Ch21.

theory2.tex Ch21 §21.1 — This module defines the shared value objects for the
generated_contracts package.  DecoratorTransformer records a single decorator
morphism in the coordinate category.  AnnotationContract records a single type
annotation together with its trust level and residual obligation.  ContractRecord
aggregates all AnnotationContracts for a single coordinate.  RegistrySection
groups ContractRecords into named sections.

theory2.tex Ch21 §21.1.1 — Trust is propagated through the annotation contract
graph from the evidence level up to the symbol level.  Copilot-proposed
annotations enter at ORACLE_PROPOSED trust (= 2).  Runtime witnesses promote
to RUNTIME_WITNESSED (= 3).  Solver-discharged obligations promote to
SOLVER_DISCHARGED (= 4).  Formal proofs achieve VERIFIED_PROOF (= 5).

All models in this module are immutable frozen dataclasses.  Updates are
performed with dataclasses.replace(...).  Collection fields use tuples, not
lists or dicts, to preserve immutability.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field, replace
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# External jugeo imports with fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, Morphism, MorphismKind,
        Site, SiteBuilder, CoveringFamily, GrothendieckTopology,
        CoordinateObject,
    )
except ImportError:
    from enum import Enum
    from dataclasses import dataclass as _dc, field as _field

    class CoordinateKind(str, Enum):
        MODULE = "MODULE"; FUNCTION = "FUNCTION"; INTERFACE = "INTERFACE"
        TEST = "TEST"; THEOREM = "THEOREM"; REGION = "REGION"

    class MorphismKind(str, Enum):
        RESTRICTION = "RESTRICTION"; INCLUSION = "INCLUSION"
        TRANSPORT = "TRANSPORT"; REFINEMENT = "REFINEMENT"

    @_dc(frozen=True)
    class Coordinate:
        components: tuple = ()
        kind: CoordinateKind = CoordinateKind.MODULE
        support_labels: frozenset = frozenset()
        metadata: dict = _field(default_factory=dict)
        def name(self): return ".".join(self.components) if self.components else ""
        def key(self): return "/".join(self.components)
        def path(self): return list(self.components)
        def depth(self): return len(self.components)
        def parent(self): return Coordinate(self.components[:-1], self.kind, frozenset(), {}) if len(self.components) > 1 else self
        def children(self): return []
        def is_prefix_of(self, other): return other.components[:len(self.components)] == self.components
        def common_ancestor(self, other):
            i = 0
            while i < len(self.components) and i < len(other.components) and self.components[i] == other.components[i]: i += 1
            return Coordinate(self.components[:i], self.kind, frozenset(), {})
        def distance_to(self, other): return len(self.components) + len(other.components) - 2 * len(self.common_ancestor(other).components)
        def serialize(self): return {"components": list(self.components), "kind": self.kind.value}
        @classmethod
        def parse(cls, d): return cls(tuple(d["components"]), CoordinateKind(d["kind"]))
        def __str__(self): return self.key()

    CoordinateObject = Coordinate

    @_dc(frozen=True)
    class Morphism:
        source: object = _field(default_factory=lambda: Coordinate())
        target: object = _field(default_factory=lambda: Coordinate())
        kind: MorphismKind = MorphismKind.INCLUSION
        label: str = ""
        def is_identity(self): return self.source == self.target
        def compose(self, other): return Morphism(self.source, other.target, self.kind, f"{self.label}·{other.label}")
        def serialize(self): return {"source": self.source.serialize(), "target": self.target.serialize(), "kind": self.kind.value, "label": self.label}

    class Site:
        def __init__(self): self._coordinates = {}; self._morphisms = []
        def coordinates(self): return list(self._coordinates.values())
        def add_coordinate(self, c): self._coordinates[c.key()] = c; return self
        def has_coordinate(self, key): return key in self._coordinates

    class SiteBuilder:
        def __init__(self): self._site = Site()
        def add(self, c): self._site.add_coordinate(c); return self
        def build(self): return self._site

    class CoveringFamily:
        def __init__(self, base, morphisms): self.base = base; self.morphisms = morphisms

    class GrothendieckTopology:
        def __init__(self, site): self.site = site

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, LocalJudgment, JudgmentBuilder, JudgmentAlgebra,
        JudgmentStatus, TrustLevel, PropositionKind,
        Proposition, Carrier, EvidenceItem, EvidenceBundle,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance,
        ProvenanceSource, EvidenceItemKind,
        _stable_hash, _now_iso,
    )
except ImportError:
    from enum import IntEnum, Enum
    from dataclasses import dataclass as _dc, field as _field
    from dataclasses import replace as _replace

    class TrustLevel(IntEnum):
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5

    class EvidenceItemKind(str, Enum):
        SOLVER_PROOF = "SOLVER_PROOF"; RUNTIME_WITNESS = "RUNTIME_WITNESS"
        ORACLE_PROPOSAL = "ORACLE_PROPOSAL"; FORMAL_PROOF = "FORMAL_PROOF"

    class PropositionKind(str, Enum):
        ASSERTION = "ASSERTION"; OBLIGATION = "OBLIGATION"; CONSTRAINT = "CONSTRAINT"

    class JudgmentStatus(str, Enum):
        OPEN = "OPEN"; CLOSED = "CLOSED"; VIOLATED = "VIOLATED"

    @_dc(frozen=True)
    class Proposition:
        text: str = ""; kind: PropositionKind = PropositionKind.ASSERTION

    @_dc(frozen=True)
    class Carrier:
        agent_id: str = ""; role: str = ""

    @_dc(frozen=True)
    class EvidenceItem:
        item_id: str = ""; kind: EvidenceItemKind = EvidenceItemKind.RUNTIME_WITNESS; content: str = ""

    @_dc(frozen=True)
    class EvidenceBundle:
        items: tuple = ()
        def is_empty(self): return len(self.items) == 0
        def strongest_kind(self): return self.items[0].kind if self.items else EvidenceItemKind.RUNTIME_WITNESS

    @_dc(frozen=True)
    class ResidualObligation:
        obligation_id: str = ""; description: str = ""; required_evidence_kind: EvidenceItemKind = EvidenceItemKind.RUNTIME_WITNESS
        deadline: str = ""; priority: int = 1; depends_on: tuple = (); is_discharged: bool = False; discharge_evidence: str = ""
        def discharge(self, evidence=""): return _replace(self, is_discharged=True, discharge_evidence=evidence)
        def is_overdue(self): return False
        def is_blocked(self): return len(self.depends_on) > 0 and not self.is_discharged
        def with_priority(self, p): return _replace(self, priority=p)
        def with_dependency(self, dep_id): return _replace(self, depends_on=self.depends_on + (dep_id,))
        def to_mapping(self): return {"obligation_id": self.obligation_id, "description": self.description, "is_discharged": self.is_discharged}

    @_dc(frozen=True)
    class Obstruction:
        obstruction_id: str = ""; description: str = ""; coordinate_key: str = ""; severity: int = 1

    @_dc(frozen=True)
    class TrustAnnotation:
        level: TrustLevel = TrustLevel.UNVERIFIED; rationale: str = ""

    @_dc(frozen=True)
    class ProvenanceSource:
        source_id: str = ""; label: str = ""

    @_dc(frozen=True)
    class Provenance:
        sources: tuple = (); chain: tuple = ()

    @_dc(frozen=True)
    class Judgment:
        coordinate: object = _field(default_factory=lambda: Coordinate())
        proposition: Proposition = _field(default_factory=Proposition)
        carrier: Carrier = _field(default_factory=Carrier)
        evidence: EvidenceBundle = _field(default_factory=EvidenceBundle)
        obligations: tuple = ()
        obstructions: tuple = ()
        trust: TrustAnnotation = _field(default_factory=TrustAnnotation)
        provenance: Provenance = _field(default_factory=Provenance)

    LocalJudgment = Judgment

    class JudgmentBuilder:
        def __init__(self): self._kwargs = {}
        def with_coordinate(self, c): self._kwargs["coordinate"] = c; return self
        def with_proposition(self, p): self._kwargs["proposition"] = p; return self
        def build(self): return Judgment(**self._kwargs)

    class JudgmentAlgebra:
        pass

    def _stable_hash(s):
        import hashlib
        return hashlib.sha256(s.encode()).hexdigest()[:16]

    def _now_iso():
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

try:
    from jugeo.solver.z3_session import (
        Z3Session, Z3QueryBuilder, Z3Result, SolveOutcome, Z3Encoder,
    )
except ImportError:
    from enum import Enum as _Enum2

    class SolveOutcome(str, _Enum2):
        SAT = "SAT"; UNSAT = "UNSAT"; UNKNOWN = "UNKNOWN"

    class Z3Result:
        def __init__(self, outcome=None, model=None): self.outcome = outcome or SolveOutcome.UNKNOWN; self.model = model

    class Z3Session:
        def check(self, constraints): return Z3Result()

    class Z3QueryBuilder:
        def __init__(self): self._constraints = []
        def add(self, c): self._constraints.append(c); return self
        def build(self): return self._constraints

    class Z3Encoder:
        def encode(self, x): return str(x)

try:
    from jugeo.evidence.channels import (
        EvidenceChannel, EvidenceRecord, EvidenceRequest, EvidenceResponse,
        ChannelRouter, CopilotChannel, SolverChannel, RuntimeChannel,
    )
except ImportError:
    from dataclasses import dataclass as _dc2, field as _field2

    class EvidenceChannel:
        TRUST_CEILING = TrustLevel.ORACLE_PROPOSED

    @_dc2
    class EvidenceRecord:
        record_id: str = ""; content: str = ""; trust: TrustLevel = TrustLevel.UNVERIFIED

    @_dc2
    class EvidenceRequest:
        request_id: str = ""; query: str = ""

    @_dc2
    class EvidenceResponse:
        response_id: str = ""; content: str = ""

    class ChannelRouter:
        def route(self, request): return EvidenceResponse()

    class CopilotChannel(EvidenceChannel):
        TRUST_CEILING = TrustLevel.ORACLE_PROPOSED
        def query_llm(self, prompt): return f"Copilot response to: {prompt}"
        def parse_response(self, response): return response

    class SolverChannel(EvidenceChannel):
        TRUST_CEILING = TrustLevel.SOLVER_DISCHARGED

    class RuntimeChannel(EvidenceChannel):
        TRUST_CEILING = TrustLevel.RUNTIME_WITNESSED

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _make_contract_id(symbol: str, annotation: str, coordinate_key: str) -> str:
    """Generate a stable 16-char hex contract ID from three input strings.

    Used for contract deduplication across analysis passes.  The same
    (symbol, annotation, coordinate_key) triple always produces the same ID,
    making it safe to re-run analysis without creating duplicate records.

    The ID is derived from the SHA-256 hash of the pipe-joined concatenation
    of the three inputs, truncated to 16 hex characters.  Copilot-scaffolded
    analysis pipelines rely on this stability to avoid re-registering
    contracts on each invocation.

    Parameters
    ----------
    symbol : str
        The name of the annotated symbol (e.g., "my_func").
    annotation : str
        The annotation string in PEP 563 form (e.g., "int", "list[str]").
    coordinate_key : str
        The slash-separated coordinate key (e.g., "jugeo/models/my_func").

    Returns
    -------
    str
        A 16-character lowercase hex string.
    """
    raw = f"{symbol}|{annotation}|{coordinate_key}"
    return _stable_hash(raw)


def _annotation_is_checkable(annotation_str: str) -> bool:
    """Return True if the annotation can be checked at runtime.

    An annotation is considered checkable when it refers to a concrete,
    resolvable type that can be passed to isinstance() or similar runtime
    checks.  Annotations that cannot be checked include:

    - ``Any`` or ``object`` (too broad to be informative)
    - ``"..."`` (ellipsis placeholder)
    - Forward-reference strings (e.g., ``'"ClassName"'`` or ``"'ClassName'"``),
      detected by checking whether the annotation string starts and ends with
      quote characters.

    Concrete types such as ``"int"``, ``"str"``, ``"list[int]"``,
    ``"Optional[int]"``, and ``"dict[str, Any]"`` are considered checkable
    because they encode real runtime constraints.

    Copilot-proposed annotations are passed through this function to determine
    whether a runtime witness can discharge the associated obligation.

    Parameters
    ----------
    annotation_str : str
        The annotation in PEP 563 string form.

    Returns
    -------
    bool
        True if the annotation is checkable at runtime.
    """
    stripped = annotation_str.strip()
    if stripped in ("Any", "object", "...", ""):
        return False
    if (stripped.startswith('"') and stripped.endswith('"')) or \
       (stripped.startswith("'") and stripped.endswith("'")):
        return False
    return True


def _trust_from_annotation(
    annotation_str: str,
    is_copilot_proposed: bool = False,
) -> TrustLevel:
    """Return the initial trust level for a given annotation string.

    Trust assignment follows the Ch21 §21.1.1 propagation rules:

    - Copilot-proposed annotations always enter at ``ORACLE_PROPOSED`` (= 2),
      regardless of the annotation content.  This enforces the copilot
      ceiling: no annotation from copilot can exceed ORACLE_PROPOSED without
      explicit evidence from a runtime witness or solver.
    - ``Any`` and ``object`` annotations are ``UNVERIFIED`` (= 1) because
      they impose no checkable constraint.
    - Annotations containing primitive types (``int``, ``str``, ``bool``,
      ``float``) are assigned ``RUNTIME_WITNESSED`` (= 3) because they can
      be verified by a simple isinstance check at runtime.
    - All other annotations default to ``UNVERIFIED`` (= 1) until evidence
      is gathered.

    Parameters
    ----------
    annotation_str : str
        The annotation in PEP 563 string form.
    is_copilot_proposed : bool
        If True, return ``ORACLE_PROPOSED`` unconditionally.

    Returns
    -------
    TrustLevel
        The initial trust level for this annotation.
    """
    if is_copilot_proposed:
        return TrustLevel.ORACLE_PROPOSED
    if annotation_str in ("Any", "object"):
        return TrustLevel.UNVERIFIED
    primitives = ("int", "str", "bool", "float")
    if any(p in annotation_str for p in primitives):
        return TrustLevel.RUNTIME_WITNESSED
    return TrustLevel.UNVERIFIED


# ---------------------------------------------------------------------------
# DecoratorTransformer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DecoratorTransformer:
    """Records a single decorator as a TRANSPORT morphism in the coordinate category.

    theory2.tex Ch21 §21.2 — A DecoratorTransformer captures the full
    algebraic description of a Python decorator: its name, the coordinate of
    the decorator itself, the input coordinate (pre-decoration), the output
    coordinate (post-decoration), the trust_delta, and parameter bindings.

    trust_delta encodes how the decorator changes the trust level of the
    symbol.  Positive deltas add evidence (lru_cache adds memoization
    witness = +1).  Negative deltas create obligations (abstractmethod
    requires an implementor = -1).  Zero deltas are semantically transparent
    (functools.wraps, classmethod, staticmethod).

    Copilot-generated decorators default to trust_delta=0 with
    is_copilot_proposed=True, which flags them for the TrustAuditor.

    Attributes
    ----------
    decorator_name : str
        Fully-qualified name of the decorator (e.g., "functools.lru_cache").
    decorator_coordinate : Coordinate
        The Coordinate of the decorator function itself.
    input_coordinate : Coordinate
        The Coordinate of the undecorated symbol.
    output_coordinate : Coordinate
        The Coordinate of the decorated symbol.
    trust_delta : int
        Change in trust level caused by this decorator.  Default 0.
    is_parametrized : bool
        True if this decorator takes arguments (e.g., @retry(max=3)).
    params : tuple
        Tuple of (name, value) pairs for parametrized decorators.
    is_copilot_proposed : bool
        True if this decorator was proposed by copilot scaffolding.
    morphism_kind : MorphismKind
        The kind of morphism (always TRANSPORT for decorators).
    notes : str
        Free-form notes added during auditing.
    """

    decorator_name: str
    decorator_coordinate: Coordinate
    input_coordinate: Coordinate
    output_coordinate: Coordinate
    trust_delta: int = 0
    is_parametrized: bool = False
    params: tuple = ()
    is_copilot_proposed: bool = False
    morphism_kind: MorphismKind = MorphismKind.TRANSPORT
    notes: str = ""

    def to_morphism(self) -> Morphism:
        """Create a Morphism representing this decorator transformation.

        Maps the decorator's action as a TRANSPORT morphism from the
        input coordinate to the output coordinate in the site category.
        The label is the decorator name for traceability.

        Returns
        -------
        Morphism
            A Morphism with source=input_coordinate, target=output_coordinate,
            kind=morphism_kind, label=decorator_name.
        """
        return Morphism(
            source=self.input_coordinate,
            target=self.output_coordinate,
            kind=self.morphism_kind,
            label=self.decorator_name,
        )

    def with_note(self, note: str) -> DecoratorTransformer:
        """Return a copy of this transformer with an appended audit note.

        Copilot audit passes use this method to attach rationale without
        mutating the original record.  Notes are space-separated.

        Parameters
        ----------
        note : str
            The note text to append.

        Returns
        -------
        DecoratorTransformer
            A new frozen instance with the note appended.
        """
        separator = " " if self.notes else ""
        return replace(self, notes=self.notes + separator + note)

    def with_trust_delta(self, delta: int) -> DecoratorTransformer:
        """Return a copy of this transformer with an updated trust_delta.

        Used by the TrustAuditor to assign or correct the trust impact of a
        decorator after static analysis or solver discharge.

        Parameters
        ----------
        delta : int
            The new trust delta value.

        Returns
        -------
        DecoratorTransformer
            A new frozen instance with trust_delta set to delta.
        """
        return replace(self, trust_delta=delta)

    def params_as_dict(self) -> dict[str, Any]:
        """Convert the params tuple to a plain dict.

        The params field stores (name, value) pairs as a tuple of 2-tuples
        to preserve immutability.  This method materialises them as a dict
        for downstream use by solver encoders and copilot context builders.

        Returns
        -------
        dict[str, Any]
            A dict mapping parameter names to their values.
        """
        return {name: value for name, value in self.params}

    def is_transparent(self) -> bool:
        """Return True if this decorator makes no observable trust change.

        A decorator is transparent when its trust_delta is zero and it was
        not proposed by copilot.  Transparent decorators (e.g., functools.wraps,
        staticmethod, classmethod) do not require solver attention.

        Returns
        -------
        bool
            True if trust_delta == 0 and not is_copilot_proposed.
        """
        return self.trust_delta == 0 and not self.is_copilot_proposed

    def applied_trust(self, baseline: TrustLevel) -> TrustLevel:
        """Compute the trust level after applying this decorator's delta.

        Clamps the result to the valid TrustLevel range [0, 5] so that
        no decorator can push trust below CONTRADICTED or above VERIFIED_PROOF.

        Parameters
        ----------
        baseline : TrustLevel
            The trust level of the symbol before decoration.

        Returns
        -------
        TrustLevel
            The clamped TrustLevel after adding trust_delta.
        """
        raw = int(baseline) + self.trust_delta
        clamped = max(0, min(5, raw))
        return TrustLevel(clamped)

    def serialize(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation of this transformer.

        All enum fields are serialised to their string values.  The params
        tuple is serialised as a list of [name, str(value)] pairs so that
        arbitrary parameter values remain JSON-safe.

        Returns
        -------
        dict[str, Any]
            A plain dict suitable for json.dumps().
        """
        return {
            "decorator_name": self.decorator_name,
            "decorator_coordinate": self.decorator_coordinate.serialize(),
            "input_coordinate": self.input_coordinate.serialize(),
            "output_coordinate": self.output_coordinate.serialize(),
            "trust_delta": self.trust_delta,
            "is_parametrized": self.is_parametrized,
            "params": [[name, str(value)] for name, value in self.params],
            "is_copilot_proposed": self.is_copilot_proposed,
            "morphism_kind": self.morphism_kind.value,
            "notes": self.notes,
        }

    def summary_line(self) -> str:
        """Return a single-line human-readable summary of this transformer.

        Format: ``"decorator_name: input/path -> output/path (Δtrust=+N)"``

        Used by logging and copilot audit report generation.

        Returns
        -------
        str
            A one-line summary string.
        """
        sign = "+" if self.trust_delta >= 0 else ""
        in_key = self.input_coordinate.key()
        out_key = self.output_coordinate.key()
        return f"{self.decorator_name}: {in_key} -> {out_key} (Δtrust={sign}{self.trust_delta})"


# ---------------------------------------------------------------------------
# AnnotationContract
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnnotationContract:
    """Records a single Python type annotation as a contract on a symbol.

    theory2.tex Ch21 §21.3 — An AnnotationContract captures the full
    semantic content of a Python type annotation: the symbol name, the
    annotation string, the coordinate of the symbol, the kind (PARAMETER,
    RETURN, CLASS_VAR), the trust level, and any residual obligation that
    must be discharged for the annotation to be fully verified.

    Copilot-proposed annotations enter at ORACLE_PROPOSED trust (= 2) and
    require explicit runtime or solver evidence to be promoted.

    Attributes
    ----------
    contract_id : str
        Stable 16-char hex ID from _make_contract_id.
    symbol_name : str
        Name of the symbol carrying the annotation.
    annotation_str : str
        The annotation as a string (PEP 563 form).
    coordinate : Coordinate
        The Coordinate of the annotated symbol.
    annotation_kind : str
        "PARAMETER", "RETURN", or "CLASS_VAR".
    trust : TrustLevel
        Current trust level for this contract.
    is_copilot_proposed : bool
        True if this annotation was proposed by copilot.
    obligation : ResidualObligation
        The residual obligation for this annotation (or empty if discharged).
    is_checkable : bool
        True if this annotation can be checked at runtime.
    notes : str
        Free-form audit notes.
    """

    contract_id: str
    symbol_name: str
    annotation_str: str
    coordinate: Coordinate
    annotation_kind: str = "PARAMETER"
    trust: TrustLevel = TrustLevel.UNVERIFIED
    is_copilot_proposed: bool = False
    obligation: ResidualObligation = field(default_factory=ResidualObligation)
    is_checkable: bool = False
    notes: str = ""

    def promote(self, new_trust: TrustLevel, rationale: str = "") -> AnnotationContract:
        """Return a copy with the trust level promoted and a note appended.

        Trust promotion records the rationale inline in the notes field so
        that audit trails are self-contained.  Copilot ceiling rules are not
        enforced here; callers should check is_copilot_ceiling_violated()
        after promotion.

        Parameters
        ----------
        new_trust : TrustLevel
            The new trust level to assign.
        rationale : str
            Optional explanation for the promotion.

        Returns
        -------
        AnnotationContract
            A new frozen instance with updated trust and appended note.
        """
        note = f" [promoted to {new_trust.name}: {rationale}]"
        return replace(self, trust=new_trust, notes=self.notes + note)

    def discharge_obligation(self, evidence: str) -> AnnotationContract:
        """Return a copy with the residual obligation discharged.

        Delegates to ResidualObligation.discharge() and returns a new
        AnnotationContract referencing the updated obligation.

        Parameters
        ----------
        evidence : str
            Description or ID of the evidence that discharges the obligation.

        Returns
        -------
        AnnotationContract
            A new frozen instance with the obligation marked discharged.
        """
        return replace(self, obligation=self.obligation.discharge(evidence))

    def with_note(self, note: str) -> AnnotationContract:
        """Return a copy with an additional audit note appended.

        Copilot audit passes attach notes to contracts during the review
        phase without mutating the original immutable record.

        Parameters
        ----------
        note : str
            The note text to append.

        Returns
        -------
        AnnotationContract
            A new frozen instance with the note appended.
        """
        separator = " " if self.notes else ""
        return replace(self, notes=self.notes + separator + note)

    def is_fully_verified(self) -> bool:
        """Return True if this contract has been fully formally verified.

        Full verification requires both VERIFIED_PROOF trust level and a
        discharged residual obligation.  A contract at VERIFIED_PROOF with
        an open obligation is considered incomplete.

        Returns
        -------
        bool
            True iff trust >= VERIFIED_PROOF and obligation.is_discharged.
        """
        return self.trust >= TrustLevel.VERIFIED_PROOF and self.obligation.is_discharged

    def is_copilot_ceiling_violated(self) -> bool:
        """Return True if this copilot contract has exceeded its trust ceiling.

        The copilot ceiling rule (Ch21 §21.1.1) states that copilot-proposed
        annotations may not exceed ORACLE_PROPOSED trust without an explicit
        evidence discharge.  If this contract was copilot-proposed, has been
        promoted above ORACLE_PROPOSED, and the obligation is still open,
        the ceiling is violated and the contract requires auditor review.

        Returns
        -------
        bool
            True iff is_copilot_proposed, trust > ORACLE_PROPOSED, and
            obligation is not discharged.
        """
        return (
            self.is_copilot_proposed
            and self.trust > TrustLevel.ORACLE_PROPOSED
            and not self.obligation.is_discharged
        )

    def serialize(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation of this contract.

        All enum fields are serialised to their value strings.  The
        obligation is serialised via its to_mapping() method.

        Returns
        -------
        dict[str, Any]
            A plain dict suitable for json.dumps().
        """
        return {
            "contract_id": self.contract_id,
            "symbol_name": self.symbol_name,
            "annotation_str": self.annotation_str,
            "coordinate": self.coordinate.serialize(),
            "annotation_kind": self.annotation_kind,
            "trust": self.trust.name,
            "is_copilot_proposed": self.is_copilot_proposed,
            "obligation": self.obligation.to_mapping(),
            "is_checkable": self.is_checkable,
            "notes": self.notes,
        }

    def to_evidence_item(self) -> EvidenceItem:
        """Create an EvidenceItem representing this contract as evidence.

        Copilot-proposed contracts are tagged as ORACLE_PROPOSAL evidence.
        All other contracts are tagged as RUNTIME_WITNESS evidence.  The
        item_id is the contract_id for stable cross-reference.

        Returns
        -------
        EvidenceItem
            An EvidenceItem suitable for inclusion in an EvidenceBundle.
        """
        kind = (
            EvidenceItemKind.ORACLE_PROPOSAL
            if self.is_copilot_proposed
            else EvidenceItemKind.RUNTIME_WITNESS
        )
        return EvidenceItem(
            item_id=self.contract_id,
            kind=kind,
            content=self.annotation_str,
        )


# ---------------------------------------------------------------------------
# ContractRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContractRecord:
    """Aggregates all AnnotationContracts for a single coordinate.

    theory2.tex Ch21 §21.1 — A ContractRecord is the sheaf section over a
    coordinate: it collects all annotation contracts for a symbol (parameters,
    return type, class variables) and provides aggregate trust and obligation
    information.

    Copilot annotation proposals are aggregated here before routing to the
    solver or runtime for discharge.

    Attributes
    ----------
    record_id : str
        Stable ID for this record.
    coordinate : Coordinate
        The Coordinate this record covers.
    contracts : tuple
        Tuple of AnnotationContract objects.
    created_at : str
        ISO timestamp of record creation.
    """

    record_id: str
    coordinate: Coordinate
    contracts: tuple = ()
    created_at: str = field(default_factory=_now_iso)

    def add_contract(self, c: AnnotationContract) -> ContractRecord:
        """Return a copy with the given contract appended.

        Immutably extends the contracts tuple.  Duplicate contract_ids are
        not checked here; callers should deduplicate using _make_contract_id
        before calling this method.

        Parameters
        ----------
        c : AnnotationContract
            The contract to append.

        Returns
        -------
        ContractRecord
            A new frozen instance with c appended to contracts.
        """
        return replace(self, contracts=self.contracts + (c,))

    def remove_contract(self, contract_id: str) -> ContractRecord:
        """Return a copy with the contract matching contract_id removed.

        If no contract matches the given ID the original record is returned
        unchanged.

        Parameters
        ----------
        contract_id : str
            The 16-char hex ID of the contract to remove.

        Returns
        -------
        ContractRecord
            A new frozen instance with the matching contract removed.
        """
        remaining = tuple(c for c in self.contracts if c.contract_id != contract_id)
        return replace(self, contracts=remaining)

    def min_trust(self) -> TrustLevel:
        """Return the minimum trust level across all contracts in this record.

        If there are no contracts, returns VERIFIED_PROOF (vacuously true:
        an empty record has no unverified annotations).

        Returns
        -------
        TrustLevel
            The lowest trust level present, or VERIFIED_PROOF if empty.
        """
        if not self.contracts:
            return TrustLevel.VERIFIED_PROOF
        return TrustLevel(min(int(c.trust) for c in self.contracts))

    def max_trust(self) -> TrustLevel:
        """Return the maximum trust level across all contracts in this record.

        If there are no contracts, returns UNVERIFIED (no evidence gathered).

        Returns
        -------
        TrustLevel
            The highest trust level present, or UNVERIFIED if empty.
        """
        if not self.contracts:
            return TrustLevel.UNVERIFIED
        return TrustLevel(max(int(c.trust) for c in self.contracts))

    def open_obligations(self) -> list[ResidualObligation]:
        """Return all residual obligations that have not yet been discharged.

        Collects the obligation from each contract where is_discharged is
        False.  Used by the obligation router to determine what work remains.

        Returns
        -------
        list[ResidualObligation]
            A list of open (undischarged) obligations.
        """
        return [c.obligation for c in self.contracts if not c.obligation.is_discharged]

    def has_copilot_ceiling_violations(self) -> bool:
        """Return True if any contract in this record violates the copilot ceiling.

        Delegates to AnnotationContract.is_copilot_ceiling_violated() for
        each contract.  Returns True on the first violation found.

        Returns
        -------
        bool
            True iff at least one contract has a copilot ceiling violation.
        """
        return any(c.is_copilot_ceiling_violated() for c in self.contracts)

    def serialize(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation of this record.

        Contracts are serialised as a list using each contract's serialize()
        method.

        Returns
        -------
        dict[str, Any]
            A plain dict suitable for json.dumps().
        """
        return {
            "record_id": self.record_id,
            "coordinate": self.coordinate.serialize(),
            "contracts": [c.serialize() for c in self.contracts],
            "created_at": self.created_at,
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary of this record.

        Format::

            ContractRecord(coord/key): N contracts, min_trust=X, max_trust=Y, K open obligations

        Used by copilot audit report generation and logging.

        Returns
        -------
        str
            A one-line summary string.
        """
        n = len(self.contracts)
        k = len(self.open_obligations())
        return (
            f"ContractRecord({self.coordinate.key()}): {n} contracts, "
            f"min_trust={self.min_trust().name}, max_trust={self.max_trust().name}, "
            f"{k} open obligations"
        )


# ---------------------------------------------------------------------------
# RegistrySection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegistrySection:
    """Groups ContractRecords into a named section of the contract registry.

    theory2.tex Ch21 §21.1 — A RegistrySection is a named collection of
    ContractRecords, typically corresponding to a Python module or package.
    Sections are assembled into a full contract registry during the static
    analysis phase.

    Copilot-generated sections are flagged and require review before being
    promoted to the production registry.

    Attributes
    ----------
    section_id : str
        Stable ID for this section.
    name : str
        Human-readable name (e.g., module path).
    records : tuple
        Tuple of ContractRecord objects.
    is_copilot_generated : bool
        True if this section was generated by copilot.
    created_at : str
        ISO timestamp.
    """

    section_id: str
    name: str
    records: tuple = ()
    is_copilot_generated: bool = False
    created_at: str = field(default_factory=_now_iso)

    def add_record(self, r: ContractRecord) -> RegistrySection:
        """Return a copy with the given ContractRecord appended.

        Immutably extends the records tuple.  Duplicate record_ids are not
        deduplicated here; callers should check before appending.

        Parameters
        ----------
        r : ContractRecord
            The record to append.

        Returns
        -------
        RegistrySection
            A new frozen instance with r appended to records.
        """
        return replace(self, records=self.records + (r,))

    def find_record(self, coordinate_key: str) -> ContractRecord | None:
        """Find and return the first ContractRecord matching the given coordinate key.

        Performs a linear scan over records comparing each record's
        coordinate.key() to the given key.  Returns None if no match is
        found.

        Parameters
        ----------
        coordinate_key : str
            The slash-separated coordinate key to search for.

        Returns
        -------
        ContractRecord or None
            The first matching record, or None if not found.
        """
        for r in self.records:
            if r.coordinate.key() == coordinate_key:
                return r
        return None

    def all_contracts(self) -> Iterator[AnnotationContract]:
        """Yield every AnnotationContract across all records in this section.

        Provides a flat iterator for operations that need to inspect all
        contracts without regard to which record they belong to.  Copilot
        audit passes use this to scan for ceiling violations section-wide.

        Yields
        ------
        AnnotationContract
            Each contract from each record, in record order.
        """
        for record in self.records:
            yield from record.contracts

    def copilot_ceiling_violations(self) -> list[str]:
        """Return the coordinate keys of all records with copilot ceiling violations.

        Collects the coordinate key of each ContractRecord where
        has_copilot_ceiling_violations() is True.

        Returns
        -------
        list[str]
            A list of coordinate key strings for records with violations.
        """
        return [
            r.coordinate.key()
            for r in self.records
            if r.has_copilot_ceiling_violations()
        ]

    def total_open_obligations(self) -> int:
        """Return the total count of open (undischarged) obligations in this section.

        Sums the open obligation counts across all records.  Used by the
        obligation dashboard to track overall section health.

        Returns
        -------
        int
            The total number of open obligations.
        """
        return sum(len(r.open_obligations()) for r in self.records)

    def merge(self, other: RegistrySection) -> RegistrySection:
        """Return a new section combining records from self and other.

        Records from other are appended after records from self.  The
        resulting section retains self's section_id, name, and created_at.
        is_copilot_generated is set to True if either section is
        copilot-generated.

        Parameters
        ----------
        other : RegistrySection
            The section whose records should be merged in.

        Returns
        -------
        RegistrySection
            A new frozen instance with combined records.
        """
        merged_records = self.records + other.records
        merged_copilot = self.is_copilot_generated or other.is_copilot_generated
        return replace(self, records=merged_records, is_copilot_generated=merged_copilot)

    def serialize(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation of this section.

        Records are serialised as a list using each record's serialize()
        method.

        Returns
        -------
        dict[str, Any]
            A plain dict suitable for json.dumps().
        """
        return {
            "section_id": self.section_id,
            "name": self.name,
            "records": [r.serialize() for r in self.records],
            "is_copilot_generated": self.is_copilot_generated,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Module summary
# ---------------------------------------------------------------------------
# This module defines the four core model types for jugeo.python_runtime.
# generated_contracts.  All types are frozen dataclasses (immutable).
# DecoratorTransformer models Ch21 §21.2 decorator morphisms.
# AnnotationContract models Ch21 §21.3 annotation sections.
# ContractRecord and RegistrySection provide the sheaf-theoretic structure.
# ---------------------------------------------------------------------------
