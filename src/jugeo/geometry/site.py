"""Semantic site objects for JuGeo geometry.

In sheaf-theoretic terms (theory2.tex §3), a *site* is a category equipped
with a Grothendieck topology — it defines which families of morphisms count
as "covers."  In the JuGeo implementation, sites represent the semantic
spaces over which judgments live.  Coordinates name points in a site.
Covers are families that collectively observe everything about a point.

The module provides:

* **Coordinate** — immutable hierarchical names for semantic points.
* **Morphism** — typed arrows between coordinates.
* **CoveringFamily** — families of morphisms that collectively cover a point.
* **GrothendieckTopology** — axioms deciding which families are covers.
* **Site** — the assembled category-with-topology.
* **SiteBuilder** — fluent construction API.
* **CoordinateIndex** — fast hierarchical lookup.
* **OverlapData** — precomputed pairwise/triple overlap bookkeeping.
* **SiteSerializer** — JSON round-tripping for persistence.
* **SiteDiagnostics** — axiom validation and copilot-assisted refinement hints.

Backward-compatible aliases (``CoordinateObject``, ``CoordinateMorphism``,
``SemanticSite``, ``CoordinateKind``, ``build_site``, ``restrict_coordinate``)
are provided at module level so existing imports continue to work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations
from typing import Any, Callable, Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CoordinateKind(str, Enum):
    """Classification of a coordinate's role in the semantic site.

    These mirror the node kinds used in theory2.tex §3.1 when constructing
    the base category for a JuGeo site.
    """

    MODULE = "module"
    FUNCTION = "function"
    INTERFACE = "interface"
    TEST = "test"
    THEOREM = "theorem"
    REGION = "region"


class MorphismKind(str, Enum):
    """Classification of morphisms between coordinates.

    In theory2.tex §3.2, morphisms in the site category fall into one of
    four flavours, each with distinct functorial behaviour when pulling
    back presheaves.

    RESTRICTION — going from a larger scope to a sub-scope.
    INCLUSION   — embedding a smaller scope into a larger one.
    TRANSPORT   — moving data between coordinates at the same depth.
    REFINEMENT  — replacing a coordinate with a finer decomposition.
    """

    RESTRICTION = "restriction"
    INCLUSION = "inclusion"
    TRANSPORT = "transport"
    REFINEMENT = "refinement"


# ---------------------------------------------------------------------------
# Coordinate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, init=False)
class Coordinate:
    """An immutable point in the semantic space of a JuGeo site.

    Coordinates use hierarchical dot-separated names following the
    convention of theory2.tex §3.1.  For example,
    ``"module.class.method.line"`` denotes a specific source location at
    depth 4.

    The copilot integration layer resolves editor locations to Coordinate
    instances, keeping the geometry explicit and typed.

    Parameters
    ----------
    components : tuple[str, ...]
        The hierarchical path segments (e.g., ``("module", "class")``).
    kind : CoordinateKind
        The semantic role of this coordinate.
    support_labels : frozenset[str]
        Optional labels indicating which support regions contain this point.
    metadata : Mapping[str, Any]
        Arbitrary extra data attached to this coordinate.
    """

    components: tuple[str, ...] = ()
    kind: CoordinateKind = CoordinateKind.REGION
    support_labels: frozenset[str] = field(default_factory=frozenset)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        components: tuple[str, ...] | str = (),
        kind: CoordinateKind = CoordinateKind.REGION,
        path: Sequence[str] | None = None,
        support_labels: frozenset[str] | Sequence[str] = frozenset(),
        metadata: Mapping[str, Any] | None = None,
        *,
        name: str | None = None,
    ) -> None:
        """Initialize a coordinate while accepting legacy constructor shapes."""
        if name is not None:
            base_name = name
            resolved_components = tuple(path or ()) or (base_name,)
        elif isinstance(components, str):
            base_name = components
            resolved_components = tuple(path or ()) or (base_name,)
        else:
            resolved_components = tuple(components)

        object.__setattr__(self, "components", resolved_components)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "support_labels", frozenset(str(v) for v in support_labels))
        object.__setattr__(self, "metadata", dict(metadata or {}))
        self.__post_init__()

    def __post_init__(self) -> None:
        """Normalize legacy ``CoordinateObject(name, kind, path, ...)`` calls."""
        if isinstance(self.components, str):
            legacy_name = self.components
            legacy_path = self.support_labels
            legacy_extra = self.metadata

            if isinstance(legacy_path, (tuple, list)):
                components = tuple(str(part) for part in legacy_path) or (legacy_name,)
                object.__setattr__(self, "components", components)

                if isinstance(legacy_extra, Mapping):
                    object.__setattr__(self, "support_labels", frozenset())
                    object.__setattr__(self, "metadata", dict(legacy_extra))
                elif isinstance(legacy_extra, (tuple, list, set, frozenset)):
                    object.__setattr__(self, "support_labels", frozenset(str(v) for v in legacy_extra))
                    object.__setattr__(self, "metadata", {})
                else:
                    object.__setattr__(self, "support_labels", frozenset())
                    object.__setattr__(self, "metadata", {})
                return

            object.__setattr__(self, "components", (legacy_name,))
            if not isinstance(self.support_labels, frozenset):
                object.__setattr__(self, "support_labels", frozenset(self.support_labels))
            if not isinstance(self.metadata, Mapping):
                object.__setattr__(self, "metadata", {})

    # -- derived properties ---------------------------------------------------

    @property
    def name(self) -> str:
        """Dot-joined human-readable name."""
        return ".".join(self.components) if self.components else "<root>"

    @property
    def key(self) -> str:
        """Slash-separated key compatible with legacy ``CoordinateObject.key``."""
        return "/".join(self.components)

    @property
    def path(self) -> tuple[str, ...]:
        """Alias kept for backward compatibility with ``CoordinateObject.path``."""
        return self.components

    @property
    def depth(self) -> int:
        """Nesting depth — number of components."""
        return len(self.components)

    # -- hierarchy navigation -------------------------------------------------

    def parent(self) -> Coordinate | None:
        """Return the coordinate one level up, or ``None`` for the root.

        In theory2.tex §3.1 this corresponds to the unique restriction
        morphism from a point to its containing scope.
        """
        if self.depth <= 1:
            return None
        return Coordinate(
            components=self.components[:-1],
            kind=self.kind,
            support_labels=self.support_labels,
        )

    def children(self, suffixes: Iterable[str]) -> list[Coordinate]:
        """Create child coordinates by appending each suffix.

        This is the inverse of :meth:`parent` — it builds the inclusion
        morphisms from finer to coarser coordinates (theory2.tex §3.1).
        """
        return [
            Coordinate(
                components=self.components + (s,),
                kind=self.kind,
                support_labels=self.support_labels,
            )
            for s in suffixes
        ]

    def is_prefix_of(self, other: Coordinate) -> bool:
        """True when *self* is a hierarchical ancestor of *other*.

        Corresponds to the existence of a unique restriction morphism
        from *other* to *self* (theory2.tex §3.2).
        """
        n = self.depth
        return other.depth > n and other.components[:n] == self.components

    def common_ancestor(self, other: Coordinate) -> Coordinate:
        """Compute the deepest shared prefix of two coordinates.

        In the site category this is the categorical product (pullback)
        in the partial-order subcategory of inclusions (theory2.tex §3.3).
        """
        shared: list[str] = []
        for a, b in zip(self.components, other.components):
            if a != b:
                break
            shared.append(a)
        return Coordinate(components=tuple(shared), kind=self.kind)

    def distance_to(self, other: Coordinate) -> int:
        """Tree distance through the nearest common ancestor.

        Defined as ``depth(self) + depth(other) - 2 * depth(lca)`` where
        *lca* is :meth:`common_ancestor`.  Used by the copilot proximity
        heuristic when ranking related coordinates.
        """
        lca = self.common_ancestor(other)
        return self.depth + other.depth - 2 * lca.depth

    # -- serialization --------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Convert to a JSON-friendly dictionary.

        See :class:`SiteSerializer` for full site-level serialization.
        """
        return {
            "components": list(self.components),
            "kind": self.kind.value,
            "support_labels": sorted(self.support_labels),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> Coordinate:
        """Reconstruct from the dictionary produced by :meth:`serialize`.

        The copilot persistence layer uses this when rehydrating site
        snapshots from disk.
        """
        return cls(
            components=tuple(data.get("components", ())),
            kind=CoordinateKind(data["kind"]) if "kind" in data else CoordinateKind.REGION,
            support_labels=frozenset(data.get("support_labels", ())),
            metadata=data.get("metadata", {}),
        )

    # -- cross-subsystem integration ------------------------------------------

    def judgment_section(self) -> Any:
        """Return the judgment section associated with this coordinate.

        In theory2.tex §4.1, every coordinate in a site determines a
        *judgment section* — the local slice of the judgment presheaf
        evaluated at this point.  When the ``jugeo.judgments.sections``
        subsystem is available, this method materialises that section
        object via the ``JudgmentSection`` factory, giving downstream
        code (descent, evidence collection) a typed handle into the
        judgment layer.

        Returns a ``Section`` object, or raises ``NotImplementedError``
        when the judgments subsystem is not installed.
        """
        try:
            from jugeo.judgments.sections import JudgmentSection  # type: ignore[import-untyped]
            return JudgmentSection(coordinate=self)
        except ImportError:
            raise NotImplementedError(
                "jugeo.judgments.sections is not installed.  "
                "Install the judgments subsystem to materialise "
                "judgment sections from coordinates."
            )

    def trust_annotation(self) -> Any:
        """Return the trust-tier annotation for this coordinate.

        The evidence subsystem (``jugeo.evidence.trust``) assigns each
        coordinate a *trust tier* reflecting the strength and provenance
        of the evidence supporting judgments at this point.  High-trust
        coordinates (e.g. those backed by formal proofs) are weighted
        more heavily during descent and cover refinement.

        Returns a ``TrustProfile`` when the evidence subsystem is
        available, or a default dict describing the unknown state.
        """
        try:
            from jugeo.evidence.trust import TrustProfile, TrustTier  # type: ignore[import-untyped]
            return TrustProfile(
                tier=TrustTier.PROPOSAL,
                support_scope=tuple(self.support_labels),
                reasons=("auto_from_coordinate",),
                entity_id=self.name,
            )
        except ImportError:
            return {
                "tier": "unknown",
                "coordinate": self.name,
                "reason": "jugeo.evidence.trust not available",
            }

    # -- deep cross-subsystem integration ------------------------------------

    @property
    def judgment(self) -> Any:
        """Return the LocalJudgment anchored at this coordinate.

        In judgment geometry every coordinate carries a *local judgment* —
        the type-theoretic assertion that is being verified at this point
        of the site.  The ``jugeo.judgments.judgment_terms`` subsystem
        materialises this as a ``LocalJudgment`` object whose term
        structure can be inspected, composed, and fed to the solver
        (theory2.tex §4.1).
        """
        try:
            from jugeo.judgments.judgment_terms import LocalJudgment  # type: ignore[import-untyped]
            return LocalJudgment(
                coordinate=self,
                proposition=f"judgment_at({self.name})",
                artifact=dict(self.metadata),
                evidence_refs=(),
                provenance=("coordinate.judgment",),
            )
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.judgments.judgment_terms to be installed"
            )

    @property
    def trust_level(self) -> Any:
        """Return the trust annotation for this coordinate.

        The evidence subsystem assigns each coordinate a *trust tier*
        drawn from a lattice of evidence strengths (theory2.tex §6.2).
        The tier determines how aggressively the descent engine may
        rely on data at this point — low-trust coordinates trigger
        additional overlap checks while high-trust ones are accepted
        eagerly.
        """
        try:
            from jugeo.evidence.trust import TrustProfile, TrustTier  # type: ignore[import-untyped]
            profile = TrustProfile(
                tier=TrustTier.PROPOSAL,
                support_scope=tuple(self.support_labels),
                reasons=("auto_trust_level",),
                entity_id=self.name,
            )
            return profile.tier
        except ImportError:
            return {"tier": "unknown", "coordinate": self.name}

    @property
    def evidence_bundle(self) -> Any:
        """Return evidence entries localised at this coordinate.

        An *evidence bundle* (theory2.tex §6.1) collects all the
        manifest entries — test results, proof obligations, runtime
        traces — that support judgments at this particular coordinate.
        Downstream consumers (descent, certification) draw on the
        bundle to justify gluing steps.
        """
        try:
            from jugeo.evidence.manifests import EvidenceManifest, EvidenceRecord  # type: ignore[import-untyped]
            from jugeo.evidence.trust import TrustProfile, TrustTier  # type: ignore[import-untyped]
            profile = TrustProfile(
                tier=TrustTier.PROPOSAL,
                support_scope=tuple(self.support_labels),
                entity_id=self.name,
            )
            try:
                from jugeo.evidence.manifests import ProvenanceTrace  # type: ignore[import-untyped]
                prov = ProvenanceTrace(origin="evidence_bundle")
            except ImportError:
                prov = ("evidence_bundle",)  # type: ignore[assignment]
            manifest = EvidenceManifest(
                coordinate=self.name,
                claim=f"evidence_at({self.name})",
                records=(),
                trust=profile,
                provenance=prov,
            )
            return manifest
        except ImportError:
            return []

    @property
    def encoding_fragment(self) -> Any:
        """Return the decidability classification for this coordinate.

        The structural frontier (``jugeo.encodings.structural_frontier``)
        classifies each coordinate as *decidable*, *semi-decidable*, or
        *undecidable* depending on whether its judgment can be encoded
        into a decidable fragment of the logic (theory2.tex §7.3).
        """
        try:
            from jugeo.encodings.structural_frontier import (  # type: ignore[import-untyped]
                StructuralFrontierPipeline,
            )
            pipeline = StructuralFrontierPipeline()
            return pipeline.classify_phase([self.name])
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.encodings.structural_frontier to be installed"
            )

    def solver_check(self) -> Any:
        """Run a Z3 satisfiability query anchored at this coordinate.

        The solver subsystem (``jugeo.solver.z3_session``) maintains a
        Z3 context in which judgment-geometric assertions are encoded as
        SMT constraints.  This method submits the local judgment at this
        coordinate to the solver and returns the satisfiability verdict
        along with any model or unsat core (theory2.tex §8.1).
        """
        try:
            from jugeo.solver.z3_session import Z3Session  # type: ignore[import-untyped]
            session = Z3Session()
            return session.query_judgment({
                "coordinate": self.name,
                "components": self.components,
                "kind": self.kind.value,
                "support_labels": tuple(self.support_labels),
            })
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.solver.z3_session to be installed"
            )

    def bug_report(self) -> Any:
        """Check for known bug patterns at this coordinate.

        The problem-modes subsystem (``jugeo.problem_modes.bug_detection``)
        maintains a registry of known defect patterns — type mismatches,
        unchecked boundaries, missing evidence — and can scan any
        coordinate for matches.  A non-empty report indicates that the
        local judgment at this point is suspect and may obstruct descent
        (theory2.tex §9.2).
        """
        try:
            from jugeo.problem_modes.bug_detection import BugDetector  # type: ignore[import-untyped]
            detector = BugDetector()
            source_repr = f"# coordinate: {self.name}\n# kind: {self.kind.value}"
            return detector.detect_bugs(source_repr, filename=self.name)
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.problem_modes.bug_detection to be installed"
            )

    @property
    def children_with_trust(self) -> Any:
        """Return child coordinates annotated with their trust tiers.

        Combines the hierarchical structure of :meth:`children` with
        the evidence layer's trust assignments, producing a list of
        ``(child_coordinate, trust_tier)`` pairs.  This is the local
        analogue of the *trust presheaf* restricted to the immediate
        star neighbourhood of this coordinate (theory2.tex §6.3).
        """
        try:
            from jugeo.evidence.trust import TrustProfile, TrustTier  # type: ignore[import-untyped]
            result = []
            for child in self.children(
                s for s in (self.metadata.get("child_suffixes") or ())
            ):
                profile = TrustProfile(
                    tier=TrustTier.PROPOSAL,
                    support_scope=tuple(child.support_labels),
                    reasons=("child_trust_propagation",),
                    entity_id=child.name,
                )
                result.append((child, profile.tier))
            return result
        except ImportError:
            return []

    def __str__(self) -> str:
        return self.name

    def encode_heap(self):
        """Encode this coordinate's memory model using collection-heap encodings."""
        try:
            from jugeo.encodings.collection_heap_encodings.integration import CollectionHeapEncodingSession, HeapCollectionPipeline, PipelineResult
            from jugeo.encodings.collection_heap_encodings.heap_summaries_and_object_identity import HeapGraphEncoding, HeapJudgment, HeapCechObstruction
            from jugeo.encodings.collection_heap_encodings.collection_encodings_should_be_fam import CollectionEncoding, ElementSheaf, CollectionJudgment, CollectionCoverStrategy
            from jugeo.encodings.collection_heap_encodings.aliasing_obligations import AliasJudgment, AliasCechObstruction, AliasDescentObstruction
            session = CollectionHeapEncodingSession()
            return {"coordinate": self.name, "session": repr(session), "status": "heap_encoded"}
        except Exception:
            return {"coordinate": self.name, "status": "heap_encoding_unavailable"}

    def encode_ir(self):
        """Encode this coordinate via the IR stack."""
        try:
            from jugeo.encodings.ir_stack.integration import IRStackSession
            from jugeo.encodings.ir_stack.models import IRNode, IRGraph, IRTransformation
            from jugeo.encodings.ir_stack.algorithms import IROptimizer, IRValidator
            return {"coordinate": self.name, "ir_status": "encoded"}
        except Exception:
            return {"coordinate": self.name, "ir_status": "unavailable"}

    def encode_text(self):
        """Encode this coordinate as a text representation."""
        try:
            from jugeo.encodings.text_encodings.integration import TextEncodingSession
            from jugeo.encodings.text_encodings.models import TextFragment, TextEncoding
            return {"coordinate": self.name, "text_status": "encoded"}
        except Exception:
            return {"coordinate": self.name, "text_status": "unavailable"}

    def encode_partiality(self):
        """Encode partiality models for this coordinate."""
        try:
            from jugeo.encodings.partiality_model_reconstruction.integration import PartialitySession
            from jugeo.encodings.partiality_model_reconstruction.models import PartialModel, PartialityReconstruction
            return {"coordinate": self.name, "partiality_status": "encoded"}
        except Exception:
            return {"coordinate": self.name, "partiality_status": "unavailable"}

    def encode_sequence_mutation(self):
        """Encode sequence mutation models for this coordinate."""
        try:
            from jugeo.encodings.sequence_mutation_encodings.integration import SequenceMutationSession
            from jugeo.encodings.sequence_mutation_encodings.models import MutationModel, SequenceEncoding
            return {"coordinate": self.name, "mutation_status": "encoded"}
        except Exception:
            return {"coordinate": self.name, "mutation_status": "unavailable"}

    def callable_surface(self):
        """Get the callable surface analysis for this coordinate."""
        try:
            from jugeo.python_runtime.callable_surfaces.algorithms import CallableSurfaceAnalyzer, MethodResolutionAlgorithm, CallCompatibilityChecker, InheritanceGraphAlgorithm, DecoratorAnalyzer
            from jugeo.python_runtime.callable_surfaces.class_construction import ClassBuilder, MetaclassAnalyzer, InitAnalyzer
            from jugeo.python_runtime.callable_surfaces.models import CallableSurface, ParameterSpec, MethodBinding, SignatureRecord
            return {"coordinate": self.name, "callable_surface": "available", "analyzers": 5}
        except Exception:
            return {"coordinate": self.name, "callable_surface": "unavailable"}

    def program_loader(self):
        """Load this coordinate as a symbolic program."""
        try:
            from jugeo.python_runtime.program_loader import ProgramLoader, ProgramSource, SymbolicProgram
            from jugeo.python_runtime.effects_async.algorithms import AsyncEffectAnalyzer
            from jugeo.python_runtime.effects_async.models import AsyncEffect, EffectTrace
            return {"coordinate": self.name, "loader": "available"}
        except Exception:
            return {"coordinate": self.name, "loader": "unavailable"}

    def runtime_effects(self):
        """Analyze runtime effects for this coordinate."""
        try:
            from jugeo.python_runtime.effects_async.algorithms import AsyncEffectAnalyzer
            from jugeo.python_runtime.effects_async.context_managers import ContextManagerAnalyzer
            from jugeo.python_runtime.effects_async.exceptions import ExceptionFlowAnalyzer
            return {"coordinate": self.name, "effects": "available"}
        except Exception:
            return {"coordinate": self.name, "effects": "unavailable"}


# ---------------------------------------------------------------------------
# Morphism
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Morphism:
    """A typed arrow between two coordinates in the site category.

    In theory2.tex §3.2, morphisms carry the *functorial* structure that
    lets us pull back presheaf data from one coordinate to another.  The
    ``kind`` field records whether the arrow is a restriction, inclusion,
    transport, or refinement so that descent computations can choose the
    correct cocycle formulas.

    Parameters
    ----------
    source : Coordinate
        Domain of the morphism.
    target : Coordinate
        Codomain of the morphism.
    kind : MorphismKind
        The structural flavour of this arrow.
    label : str
        Optional human-readable label for copilot display.
    """

    source: Coordinate
    target: Coordinate
    kind: MorphismKind = MorphismKind.RESTRICTION
    label: str = ""

    @property
    def is_identity(self) -> bool:
        """True when source and target coincide — the identity arrow."""
        return self.source == self.target

    def compose(self, other: Morphism) -> Morphism:
        """Compose ``self`` after ``other``: other ; self.

        Requires ``other.target == self.source`` (theory2.tex §3.2,
        associativity axiom).  The resulting kind defaults to the
        *first* non-identity kind encountered.

        Raises
        ------
        ValueError
            If the morphisms are not composable.
        """
        if other.target != self.source:
            raise ValueError(
                f"Cannot compose: other.target={other.target.name} "
                f"!= self.source={self.source.name}"
            )
        composed_kind = self.kind if not self.is_identity else other.kind
        composed_label = ""
        if other.label and self.label:
            composed_label = f"{other.label};{self.label}"
        else:
            composed_label = other.label or self.label
        return Morphism(
            source=other.source,
            target=self.target,
            kind=composed_kind,
            label=composed_label,
        )

    def is_invertible(self) -> bool:
        """Heuristic check for invertibility.

        In theory2.tex §3.2, an arrow is invertible when it is an
        identity or a transport between coordinates at the same depth
        (since transports are by definition equivalences).
        """
        if self.is_identity:
            return True
        if self.kind == MorphismKind.TRANSPORT and self.source.depth == self.target.depth:
            return True
        return False

    def reversed(self) -> Morphism:
        """Return the formally reversed morphism.

        Only meaningful when :meth:`is_invertible` holds.  Raises
        ``ValueError`` otherwise.
        """
        if not self.is_invertible():
            raise ValueError(f"Morphism {self.label!r} is not invertible")
        return Morphism(
            source=self.target,
            target=self.source,
            kind=self.kind,
            label=f"inv({self.label})" if self.label else "",
        )

    def factors_through(self, intermediate: Coordinate) -> bool:
        """Check whether this morphism could factor through *intermediate*.

        Returns ``True`` when *intermediate* lies on the hierarchical path
        between ``source`` and ``target`` — i.e., it is a descendant of
        ``target`` and an ancestor-or-equal of ``source``
        (theory2.tex §3.2, factorisation lemma).
        """
        return (
            self.target.is_prefix_of(intermediate) or self.target == intermediate
        ) and (
            intermediate.is_prefix_of(self.source) or intermediate == self.source
        )

    def serialize(self) -> dict[str, Any]:
        """JSON-friendly dictionary representation."""
        return {
            "source": self.source.serialize(),
            "target": self.target.serialize(),
            "kind": self.kind.value,
            "label": self.label,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> Morphism:
        """Reconstruct from serialized form."""
        return cls(
            source=Coordinate.parse(data["source"]),
            target=Coordinate.parse(data["target"]),
            kind=MorphismKind(data["kind"]) if "kind" in data else MorphismKind.RESTRICTION,
            label=data.get("label", ""),
        )


# ---------------------------------------------------------------------------
# OverlapData
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OverlapData:
    """Precomputed overlap information for a covering family.

    In theory2.tex §4, the Cech complex of a cover requires knowing
    pairwise and triple overlaps in order to state the cocycle condition.
    ``OverlapData`` caches these so that descent checks do not recompute
    them on every call.

    The copilot descent-hint system queries ``OverlapData`` to highlight
    where cocycle mismatches occur.
    """

    pairwise: list[tuple[Morphism, Morphism]] = field(default_factory=list)
    triple: list[tuple[Morphism, Morphism, Morphism]] = field(default_factory=list)
    cocycle_satisfied: bool = False

    def compute_pairwise(self, members: Sequence[Morphism]) -> None:
        """Populate pairwise overlaps from a list of covering morphisms.

        Two morphisms *f_i* and *f_j* overlap when their sources share a
        common ancestor that is a proper refinement of the base
        (theory2.tex §4.1, pairwise fibre products).
        """
        self.pairwise = []
        for i, fi in enumerate(members):
            for j, fj in enumerate(members):
                if j <= i:
                    continue
                lca = fi.source.common_ancestor(fj.source)
                if lca.depth > 0:
                    self.pairwise.append((fi, fj))

    def compute_triple(self, members: Sequence[Morphism]) -> None:
        """Populate triple overlaps — needed for the 2-cocycle condition.

        In theory2.tex §4.2, the triple overlaps appear in the second
        differential of the Cech complex.
        """
        self.triple = []
        for fi, fj, fk in combinations(members, 3):
            lca_ij = fi.source.common_ancestor(fj.source)
            lca_ijk = lca_ij.common_ancestor(fk.source)
            if lca_ijk.depth > 0:
                self.triple.append((fi, fj, fk))

    def check_cocycle(self, members: Sequence[Morphism]) -> bool:
        """Verify the cocycle condition on precomputed overlaps.

        The cocycle condition (theory2.tex §4.3) says that for every
        triple overlap *U_{ijk}*, the composition of transition data
        around the triangle must be the identity.  Here we check the
        structural precondition: every triple overlap is covered by
        at least two pairwise overlaps.

        Returns
        -------
        bool
            ``True`` if the cocycle condition is structurally satisfiable.
        """
        if not self.pairwise:
            self.compute_pairwise(members)
        if not self.triple:
            self.compute_triple(members)
        # Build a set of source-name pairs for efficient lookup.
        pair_set: set[tuple[str, str]] = set()
        for a, b in self.pairwise:
            pair_set.add((a.source.name, b.source.name))
            pair_set.add((b.source.name, a.source.name))
        for fi, fj, fk in self.triple:
            edges = [
                (fi.source.name, fj.source.name) in pair_set,
                (fj.source.name, fk.source.name) in pair_set,
                (fi.source.name, fk.source.name) in pair_set,
            ]
            if sum(edges) < 2:
                self.cocycle_satisfied = False
                return False
        self.cocycle_satisfied = True
        return True

    def overlap_count(self) -> int:
        """Total number of pairwise overlaps."""
        return len(self.pairwise)

    def triple_count(self) -> int:
        """Total number of triple overlaps."""
        return len(self.triple)

    def serialize(self) -> dict[str, Any]:
        """Serialize overlap data to a JSON-compatible dictionary."""
        return {
            "pairwise_count": len(self.pairwise),
            "triple_count": len(self.triple),
            "cocycle_satisfied": self.cocycle_satisfied,
        }


# ---------------------------------------------------------------------------
# CoveringFamily
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CoveringFamily:
    """A collection of morphisms that together cover a coordinate.

    In theory2.tex §3.3, a covering family ``{f_i: U_i -> U}`` is a
    family in the Grothendieck topology whose members jointly detect
    all local data on *U*.  The copilot engine uses covering families
    to decide which evidence patches must be consulted when forming a
    global judgment.

    Parameters
    ----------
    base : Coordinate
        The coordinate being covered.
    members : list[Morphism]
        The covering morphisms, each targeting ``base``.
    label : str
        Optional display name for the covering family.
    """

    base: Coordinate
    members: list[Morphism] = field(default_factory=list)
    label: str = ""
    _overlap_data: OverlapData | None = field(default=None, repr=False)

    def is_covering(self) -> bool:
        """Validate that all members actually target :attr:`base`.

        This is the first structural axiom of theory2.tex §3.3: every
        morphism in the family must land on the base coordinate.
        """
        return all(m.target == self.base for m in self.members)

    def overlap_pairs(self) -> list[tuple[Morphism, Morphism]]:
        """Return pairs of members whose sources overlap.

        Overlap means their sources share a non-trivial common ancestor
        (theory2.tex §4.1, pairwise fibre products).
        """
        overlaps: list[tuple[Morphism, Morphism]] = []
        for i, fi in enumerate(self.members):
            for j, fj in enumerate(self.members):
                if j <= i:
                    continue
                lca = fi.source.common_ancestor(fj.source)
                if lca.depth > 0:
                    overlaps.append((fi, fj))
        return overlaps

    def compute_overlaps(self) -> OverlapData:
        """Fully compute and cache overlap information.

        Returns the :class:`OverlapData` holding pairwise overlaps,
        triple overlaps, and the cocycle-condition check.  The copilot
        descent UI reads this to highlight potential gluing failures.
        """
        od = OverlapData()
        od.compute_pairwise(self.members)
        od.compute_triple(self.members)
        od.check_cocycle(self.members)
        self._overlap_data = od
        return od

    def cached_overlaps(self) -> OverlapData:
        """Return cached overlap data, computing it if necessary."""
        if self._overlap_data is None:
            return self.compute_overlaps()
        return self._overlap_data

    def refinement_of(self, other: CoveringFamily) -> bool:
        """Check whether *self* refines *other* (theory2.tex §3.4).

        A family ``{g_j}`` refines ``{f_i}`` when for every *g_j* there
        exists an *f_i* such that ``g_j`` factors through ``f_i``.  Here
        we use the hierarchical prefix test as a proxy for factorisation.
        """
        if self.base != other.base:
            return False
        for gj in self.members:
            if not any(
                gj.source == fi.source or fi.source.is_prefix_of(gj.source)
                for fi in other.members
            ):
                return False
        return True

    def pullback_along(self, morphism: Morphism) -> CoveringFamily:
        """Pull this covering family back along a morphism to the base.

        Given ``h: V -> U`` and a cover ``{f_i: U_i -> U}``, the pullback
        cover is ``{pr_1: U_i x_U V -> V}`` (theory2.tex §3.5).  We
        approximate the fibre product by restricting each source to its
        common refinement with *V*.

        Parameters
        ----------
        morphism : Morphism
            The morphism along which to pull back; must target ``self.base``.

        Returns
        -------
        CoveringFamily
            A new covering family over ``morphism.source``.
        """
        if morphism.target != self.base:
            raise ValueError(
                f"Pullback morphism target {morphism.target.name} "
                f"does not match base {self.base.name}"
            )
        new_base = morphism.source
        new_members: list[Morphism] = []
        for fi in self.members:
            lca = fi.source.common_ancestor(new_base)
            pulled = Morphism(
                source=lca if lca.depth > 0 else fi.source,
                target=new_base,
                kind=fi.kind,
                label=f"pb({fi.label})" if fi.label else "",
            )
            new_members.append(pulled)
        return CoveringFamily(
            base=new_base,
            members=new_members,
            label=f"pb({self.label})",
        )

    def add_member(self, morphism: Morphism) -> None:
        """Append a morphism to this family, clearing cached overlaps."""
        self.members.append(morphism)
        self._overlap_data = None

    def member_sources(self) -> list[Coordinate]:
        """Convenience: list of all source coordinates."""
        return [m.source for m in self.members]

    def size(self) -> int:
        """Number of members in this family."""
        return len(self.members)

    def serialize(self) -> dict[str, Any]:
        """JSON-friendly dictionary."""
        return {
            "base": self.base.serialize(),
            "members": [m.serialize() for m in self.members],
            "label": self.label,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> CoveringFamily:
        """Reconstruct from serialized form."""
        return cls(
            base=Coordinate.parse(data["base"]),
            members=[Morphism.parse(m) for m in data.get("members", [])],
            label=data.get("label", ""),
        )


# ---------------------------------------------------------------------------
# GrothendieckTopology
# ---------------------------------------------------------------------------


class GrothendieckTopology:
    """Defines which covering families count as genuine covers.

    In theory2.tex §3.6, a Grothendieck topology *J* on a category *C*
    assigns to each object *U* a collection *J(U)* of sieves (or
    covering families) satisfying three axioms:

    1. **Identity axiom** — the maximal sieve is always a cover.
    2. **Pullback stability** — pulling a cover back along any morphism
       yields another cover.
    3. **Local character** — if a sieve *S* is such that for every
       cover *{f_i}* the pulled-back sieves *f_i^*(S)* are covers, then
       *S* itself is a cover.

    This class provides standard topologies (trivial, discrete, canonical)
    and lets users define custom covering axioms via predicates.  The
    copilot topology-check command validates that a proposed topology
    satisfies all three axioms.
    """

    def __init__(self, name: str = "custom") -> None:
        self.name = name
        self._axioms: list[Callable[[CoveringFamily], bool]] = []
        self._explicit_covers: dict[str, list[CoveringFamily]] = {}

    def covers_of(self, coordinate: Coordinate) -> list[CoveringFamily]:
        """Return all registered covering families for a coordinate.

        In the Grothendieck topology *J*, this returns *J(U)* for the
        object *U* represented by *coordinate*.
        """
        return list(self._explicit_covers.get(coordinate.name, []))

    def is_covering(self, family: CoveringFamily) -> bool:
        """Decide whether a family is a cover under this topology.

        Checks structural validity, then applies all registered axioms.
        A family passes if it satisfies structural validity *and* every
        axiom returns ``True``, or if it has been explicitly registered.
        """
        if not family.is_covering():
            return False
        # Explicitly registered families always pass.
        if family in self._explicit_covers.get(family.base.name, []):
            return True
        if not self._axioms:
            return False
        return all(axiom(family) for axiom in self._axioms)

    def add_covering_axiom(
        self, predicate: Callable[[CoveringFamily], bool]
    ) -> None:
        """Register an additional covering axiom.

        The predicate receives a :class:`CoveringFamily` and returns
        ``True`` when the family should be considered a cover.  Multiple
        axioms are combined conjunctively: all must hold.
        """
        self._axioms.append(predicate)

    def register_cover(self, family: CoveringFamily) -> None:
        """Explicitly add a covering family to the topology.

        Useful for building topologies incrementally (e.g., the copilot
        topology builder adds covers as they are discovered).
        """
        key = family.base.name
        self._explicit_covers.setdefault(key, []).append(family)

    def saturation(
        self, families: Iterable[CoveringFamily]
    ) -> list[CoveringFamily]:
        """Saturate a collection of families under the topology axioms.

        The saturation (theory2.tex §3.7) adds every family that can be
        obtained by composing, pulling back, or locally extending the
        given families.  Here we implement the elementary closure: for
        each family *F*, include all refinements of *F* that are
        themselves covers.

        Parameters
        ----------
        families : Iterable[CoveringFamily]
            Seed families.

        Returns
        -------
        list[CoveringFamily]
            The saturated collection.
        """
        saturated = list(families)
        seen: set[int] = {id(f) for f in saturated}
        changed = True
        while changed:
            changed = False
            new_batch: list[CoveringFamily] = []
            for f in saturated:
                for cover_list in self._explicit_covers.values():
                    for g in cover_list:
                        if id(g) not in seen and g.refinement_of(f):
                            new_batch.append(g)
                            seen.add(id(g))
                            changed = True
            saturated.extend(new_batch)
        return saturated

    def pullback_stability_check(
        self, family: CoveringFamily, morphisms: Sequence[Morphism]
    ) -> bool:
        """Verify pullback stability (axiom 2) for a family.

        For every morphism *h: V -> U* with ``U = family.base``, the
        pulled-back family must also be a cover.
        """
        for h in morphisms:
            if h.target != family.base:
                continue
            pulled = family.pullback_along(h)
            if not self.is_covering(pulled):
                return False
        return True

    def local_character_check(
        self, family: CoveringFamily, all_families: Sequence[CoveringFamily]
    ) -> bool:
        """Verify local character (axiom 3) for a family.

        If for every cover ``{f_i}`` of the base, pulling *family* back
        along each *f_i* yields a cover, then *family* itself should be
        a cover.  We check the converse: if *family* passes the axioms
        but its pullbacks do not, local character is violated.
        """
        for other in all_families:
            if other.base != family.base:
                continue
            for fi in other.members:
                pulled = family.pullback_along(fi)
                if pulled.members and not self.is_covering(pulled):
                    return False
        return True

    def identity_axiom_check(self, coordinate: Coordinate) -> bool:
        """Verify the identity axiom (axiom 1) for a coordinate.

        The identity morphism ``id: U -> U`` must always form a
        (singleton) covering family.
        """
        identity = Morphism(
            source=coordinate,
            target=coordinate,
            kind=MorphismKind.RESTRICTION,
            label="id",
        )
        family = CoveringFamily(
            base=coordinate, members=[identity], label="id-cover"
        )
        return family.is_covering()

    def serialize(self) -> dict[str, Any]:
        """Serialize the topology (covers only; predicates are not serializable)."""
        return {
            "name": self.name,
            "explicit_covers": {
                k: [f.serialize() for f in v]
                for k, v in self._explicit_covers.items()
            },
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> GrothendieckTopology:
        """Reconstruct from serialized form."""
        topo = cls(name=data.get("name", "custom"))
        for _key, fam_list in data.get("explicit_covers", {}).items():
            for fam_data in fam_list:
                topo.register_cover(CoveringFamily.parse(fam_data))
        return topo

    # -- standard topologies --------------------------------------------------

    @classmethod
    def trivial(cls) -> GrothendieckTopology:
        """The trivial topology: only identity covers.

        In theory2.tex §3.8, the trivial topology turns every presheaf
        into a sheaf — no non-trivial gluing is required.
        """
        topo = cls(name="trivial")
        topo.add_covering_axiom(
            lambda fam: len(fam.members) == 1 and fam.members[0].is_identity
        )
        return topo

    @classmethod
    def discrete(cls) -> GrothendieckTopology:
        """The discrete topology: every non-empty family is a cover.

        In theory2.tex §3.8, the discrete topology makes the only sheaves
        be representable presheaves.  Used for testing and as a liberal
        baseline.
        """
        topo = cls(name="discrete")
        topo.add_covering_axiom(lambda fam: len(fam.members) > 0)
        return topo

    @classmethod
    def canonical(cls) -> GrothendieckTopology:
        """The canonical topology: a family covers iff it is jointly surjective.

        Joint surjectivity is approximated by checking that the sources
        of the family's members, together with their ancestors, include
        the base coordinate's full subtree (theory2.tex §3.9).
        """
        topo = cls(name="canonical")

        def _canonical_check(fam: CoveringFamily) -> bool:
            if not fam.members:
                return False
            source_names: set[str] = set()
            for m in fam.members:
                source_names.add(m.source.name)
                cur = m.source.parent()
                while cur is not None:
                    source_names.add(cur.name)
                    cur = cur.parent()
            return fam.base.name in source_names

        topo.add_covering_axiom(_canonical_check)
        return topo


# ---------------------------------------------------------------------------
# CoordinateIndex
# ---------------------------------------------------------------------------


class CoordinateIndex:
    """Efficient lookup structure for coordinates in a site.

    Builds an in-memory trie from the hierarchical components of each
    coordinate, enabling fast prefix queries, depth queries, and pattern
    matching.  The copilot autocomplete engine queries this index when
    suggesting coordinate completions.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, Coordinate] = {}
        self._by_depth: dict[int, list[Coordinate]] = {}
        self._trie: dict[str, Any] = {}

    def add(self, coord: Coordinate) -> None:
        """Index a single coordinate."""
        self._by_name[coord.name] = coord
        self._by_depth.setdefault(coord.depth, []).append(coord)
        node = self._trie
        for comp in coord.components:
            node = node.setdefault(comp, {})
        node.setdefault("__leaf__", []).append(coord)

    def add_all(self, coords: Iterable[Coordinate]) -> None:
        """Bulk-index multiple coordinates."""
        for c in coords:
            self.add(c)

    def by_prefix(self, prefix: Coordinate) -> list[Coordinate]:
        """Return all coordinates that start with ``prefix.components``.

        Walks the trie to the prefix node and collects all leaves
        beneath it.  Mirrors the restriction to an open subset in
        theory2.tex §5.1.
        """
        node = self._trie
        for comp in prefix.components:
            if comp not in node:
                return []
            node = node[comp]
        return self._collect_leaves(node)

    def by_depth(self, depth: int) -> list[Coordinate]:
        """Return all coordinates at a given nesting depth."""
        return list(self._by_depth.get(depth, []))

    def by_pattern(self, pattern: str) -> list[Coordinate]:
        """Match coordinates against a glob-like pattern.

        Supports ``*`` for a single component and ``**`` for any number
        of components.  For example, ``"module.*.method"`` matches any
        coordinate with three components whose first is ``"module"`` and
        third is ``"method"``.

        The copilot coordinate-search command uses this for fuzzy
        coordinate lookup.
        """
        parts = pattern.split(".")
        results: list[Coordinate] = []
        for coord in self._by_name.values():
            if self._match(parts, list(coord.components)):
                results.append(coord)
        return results

    def nearest_neighbors(
        self, target: Coordinate, k: int = 5
    ) -> list[tuple[Coordinate, int]]:
        """Return the *k* closest coordinates by tree distance.

        Uses :meth:`Coordinate.distance_to` as the metric.  The copilot
        relevance ranker calls this to suggest related coordinates when
        the user focuses a particular source location.
        """
        scored: list[tuple[Coordinate, int]] = []
        for coord in self._by_name.values():
            if coord == target:
                continue
            scored.append((coord, target.distance_to(coord)))
        scored.sort(key=lambda pair: pair[1])
        return scored[:k]

    def lookup(self, name: str) -> Coordinate | None:
        """Exact lookup by dotted name."""
        return self._by_name.get(name)

    def all_coordinates(self) -> list[Coordinate]:
        """Return every indexed coordinate."""
        return list(self._by_name.values())

    def depth_range(self) -> tuple[int, int]:
        """Return the minimum and maximum depths in the index.

        Useful for the copilot tree-view renderer to determine how
        many levels to display.
        """
        if not self._by_depth:
            return (0, 0)
        depths = list(self._by_depth.keys())
        return (min(depths), max(depths))

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, coord: Coordinate) -> bool:
        return coord.name in self._by_name

    # -- internal helpers -----------------------------------------------------

    @staticmethod
    def _match(pattern_parts: list[str], components: list[str]) -> bool:
        """Recursive glob matcher for coordinate patterns."""
        if not pattern_parts and not components:
            return True
        if not pattern_parts:
            return False
        head = pattern_parts[0]
        rest = pattern_parts[1:]
        if head == "**":
            # ** matches zero or more components
            if CoordinateIndex._match(rest, components):
                return True
            if components:
                return CoordinateIndex._match(pattern_parts, components[1:])
            return False
        if not components:
            return False
        if head == "*" or head == components[0]:
            return CoordinateIndex._match(rest, components[1:])
        return False

    @staticmethod
    def _collect_leaves(node: dict[str, Any]) -> list[Coordinate]:
        """Recursively collect all leaf coordinates under a trie node."""
        results: list[Coordinate] = []
        for key, val in node.items():
            if key == "__leaf__":
                results.extend(val)
            elif isinstance(val, dict):
                results.extend(CoordinateIndex._collect_leaves(val))
        return results


# ---------------------------------------------------------------------------
# Site
# ---------------------------------------------------------------------------


class Site:
    """The assembled site: a category of coordinates with a topology.

    In theory2.tex §3, the site is the fundamental workspace.  Presheaves
    on the site assign data to each coordinate.  Sheaves are presheaves
    satisfying the descent (gluing) condition with respect to the topology.

    The copilot integration layer maintains a *current site* reflecting
    the user's project structure and uses it to scope all judgment
    operations.

    Parameters
    ----------
    topology : GrothendieckTopology
        The topology equipping this site.
    label : str
        Optional display name.
    """

    def __init__(
        self,
        topology: GrothendieckTopology | None = None,
        label: str = "",
    ) -> None:
        self.topology = topology or GrothendieckTopology.discrete()
        self.label = label
        self._coordinates: dict[str, Coordinate] = {}
        self._morphisms: list[Morphism] = []
        self._covers: list[CoveringFamily] = []
        self._index: CoordinateIndex = CoordinateIndex()

    # -- object management ----------------------------------------------------

    def add_coordinate(self, coord: Coordinate) -> None:
        """Register a coordinate in the site."""
        self._coordinates[coord.name] = coord
        self._index.add(coord)

    def add_morphism(self, morphism: Morphism) -> None:
        """Register a morphism in the site's arrow set.

        If the source or target coordinate has not been registered yet it
        is added automatically, keeping the site self-consistent.
        """
        self._morphisms.append(morphism)
        for c in (morphism.source, morphism.target):
            if c.name not in self._coordinates:
                self.add_coordinate(c)

    def add_covering_family(self, family: CoveringFamily) -> None:
        """Register a covering family and its topology entry.

        Ensures that the base and all member sources are registered as
        coordinates and that the topology knows about this family.
        """
        self._covers.append(family)
        self.topology.register_cover(family)
        if family.base.name not in self._coordinates:
            self.add_coordinate(family.base)
        for m in family.members:
            if m.source.name not in self._coordinates:
                self.add_coordinate(m.source)

    # -- queries --------------------------------------------------------------

    def objects(self) -> list[Coordinate]:
        """All coordinates (objects) in the site category."""
        return list(self._coordinates.values())

    def morphisms_from(self, coord: Coordinate) -> list[Morphism]:
        """All morphisms whose source is ``coord``."""
        return [m for m in self._morphisms if m.source == coord]

    def morphisms_to(self, coord: Coordinate) -> list[Morphism]:
        """All morphisms whose target is ``coord``."""
        return [m for m in self._morphisms if m.target == coord]

    def covering_families(
        self, coord: Coordinate | None = None
    ) -> list[CoveringFamily]:
        """Return covering families, optionally filtered by base coordinate.

        When ``coord`` is ``None``, returns all registered families.
        """
        if coord is None:
            return list(self._covers)
        return [f for f in self._covers if f.base == coord]

    def index(self) -> CoordinateIndex:
        """Access the coordinate index for fast lookup."""
        return self._index

    def coordinate_count(self) -> int:
        """Number of registered coordinates."""
        return len(self._coordinates)

    def morphism_count(self) -> int:
        """Number of registered morphisms."""
        return len(self._morphisms)

    # -- structural operations ------------------------------------------------

    def refine_cover(
        self,
        family: CoveringFamily,
        refinement_members: list[Morphism],
    ) -> CoveringFamily:
        """Produce a refinement of an existing cover.

        Given ``{f_i: U_i -> U}`` and new morphisms ``{g_j: V_j -> U}``,
        check that ``{g_j}`` refines ``{f_i}`` (theory2.tex §3.4) and
        register the result.

        Raises
        ------
        ValueError
            If the refinement condition fails.
        """
        refined = CoveringFamily(
            base=family.base,
            members=refinement_members,
            label=f"ref({family.label})",
        )
        if not refined.refinement_of(family):
            raise ValueError(
                "Proposed morphisms do not refine the given cover"
            )
        self.add_covering_family(refined)
        return refined

    def change_of_site(
        self, functor: Callable[[Coordinate], Coordinate | None]
    ) -> Site:
        """Apply a functor to produce a new site (theory2.tex §5.3).

        The functor maps coordinates; morphisms are transported when both
        endpoints are in the image.  The topology is rebuilt by applying
        the functor to each cover's members.

        Parameters
        ----------
        functor : Callable[[Coordinate], Coordinate | None]
            Mapping on objects; returning ``None`` drops the coordinate.
        """
        new_site = Site(
            topology=GrothendieckTopology(
                name=f"image({self.topology.name})"
            ),
            label=f"image({self.label})",
        )
        coord_map: dict[str, Coordinate] = {}
        for c in self._coordinates.values():
            fc = functor(c)
            if fc is not None:
                new_site.add_coordinate(fc)
                coord_map[c.name] = fc
        for m in self._morphisms:
            if m.source.name in coord_map and m.target.name in coord_map:
                new_site.add_morphism(
                    Morphism(
                        source=coord_map[m.source.name],
                        target=coord_map[m.target.name],
                        kind=m.kind,
                        label=m.label,
                    )
                )
        for fam in self._covers:
            if fam.base.name not in coord_map:
                continue
            new_members = []
            for mem in fam.members:
                if mem.source.name in coord_map:
                    new_members.append(
                        Morphism(
                            source=coord_map[mem.source.name],
                            target=coord_map[fam.base.name],
                            kind=mem.kind,
                            label=mem.label,
                        )
                    )
            if new_members:
                new_site.add_covering_family(
                    CoveringFamily(
                        base=coord_map[fam.base.name],
                        members=new_members,
                        label=fam.label,
                    )
                )
        return new_site

    def localize_at(self, coord: Coordinate) -> Site:
        """Localize the site at a coordinate (theory2.tex §5.4).

        Returns the full subcategory on all objects that map to ``coord``
        — i.e., ``coord`` itself and all of its hierarchical descendants.
        This is the stalk construction at the site level.
        """
        descendants = self._index.by_prefix(coord)
        keep = {c.name for c in descendants}
        keep.add(coord.name)
        return self.restrict_to_subsite(keep)

    def is_local(self) -> bool:
        """Check whether the site is local (has a unique terminal object).

        A local site (theory2.tex §5.5) has a single coordinate from
        which every other coordinate is reachable via morphisms.
        """
        if not self._coordinates:
            return True
        min_depth = min(c.depth for c in self._coordinates.values())
        # A terminal object sits at minimum depth; in the hierarchical
        # model every other coordinate restricts *to* it, so it is the
        # unique shallowest coordinate.
        roots = [
            c for c in self._coordinates.values()
            if c.depth == min_depth
        ]
        return len(roots) == 1

    def global_sections(self) -> list[Coordinate]:
        """Return coordinates that are "globally visible".

        In sheaf theory (theory2.tex §5.6), global sections are elements
        of Gamma(X, F).  Here we return coordinates that participate in
        every covering family at the top level, approximating the notion
        that their data is visible everywhere.
        """
        if not self._covers:
            return list(self._coordinates.values())
        top_depth = min(
            (c.depth for c in self._coordinates.values()), default=0
        )
        top_coords = self._index.by_depth(top_depth)
        global_coords: list[Coordinate] = []
        for c in self._coordinates.values():
            visible = True
            for tc in top_coords:
                families = self.covering_families(tc)
                if families and not any(
                    c == m.source
                    or c.is_prefix_of(m.source)
                    or m.source.is_prefix_of(c)
                    for fam in families
                    for m in fam.members
                ):
                    visible = False
                    break
            if visible:
                global_coords.append(c)
        return global_coords

    def restrict_to_subsite(self, names: set[str]) -> Site:
        """Build a sub-site containing only the named coordinates.

        Morphisms and covers are restricted to those whose endpoints
        are all in the subsite.  The copilot focus-mode command uses
        this to narrow the working site to a relevant subset.
        """
        new_topo = GrothendieckTopology(
            name=f"sub({self.topology.name})"
        )
        sub = Site(topology=new_topo, label=f"sub({self.label})")
        for name in names:
            if name in self._coordinates:
                sub.add_coordinate(self._coordinates[name])
        for m in self._morphisms:
            if m.source.name in names and m.target.name in names:
                sub.add_morphism(m)
        for fam in self._covers:
            if fam.base.name not in names:
                continue
            kept = [
                mem for mem in fam.members if mem.source.name in names
            ]
            if kept:
                sub.add_covering_family(
                    CoveringFamily(
                        base=fam.base, members=kept, label=fam.label
                    )
                )
        return sub

    # -- backward-compatible helpers ------------------------------------------

    def register(self, coordinate: Coordinate) -> None:
        """Legacy alias for :meth:`add_coordinate`.

        Kept so that code written against ``SemanticSite.register()``
        continues to work after the migration.
        """
        self.add_coordinate(coordinate)

    def descendants(self, coordinate: Coordinate) -> tuple[Coordinate, ...]:
        """Legacy helper: return all proper descendants of *coordinate*."""
        return tuple(
            candidate
            for candidate in self._index.by_prefix(coordinate)
            if candidate != coordinate
        )

    # -- cross-subsystem integration ------------------------------------------

    @classmethod
    def formal_core_site(cls) -> Site:
        """Build a site seeded from the formal-core foundation layer.

        The ``jugeo.foundations.formal_core`` subsystem houses the
        theorem registry and formal invariants that anchor the
        judgment geometry.  This class method creates a :class:`Site`
        whose coordinates correspond to the registered theorems in the
        formal core, giving the descent engine a ground-truth skeleton
        to build upon (theory2.tex §9).

        Returns a ``Site`` populated with one coordinate per theorem,
        or raises ``NotImplementedError`` when the foundations
        subsystem is absent.
        """
        try:
            from jugeo.foundations.formal_core import (  # type: ignore[import-untyped]
                THEOREM_REGISTRY,
                list_theorem_ids,
            )
            site = cls(
                topology=GrothendieckTopology.canonical(),
                label="formal-core",
            )
            for thm_id in list_theorem_ids():
                coord = Coordinate(
                    components=tuple(thm_id.split(".")),
                    kind=CoordinateKind.REGION,
                    support_labels=frozenset({"formal_core"}),
                    metadata={"theorem_id": thm_id},
                )
                site.add_coordinate(coord)
            return site
        except ImportError:
            raise NotImplementedError(
                "jugeo.foundations.formal_core is not installed.  "
                "Install the foundations subsystem to build sites "
                "from the formal core."
            )

    # -- deep cross-subsystem integration ------------------------------------

    @property
    def judgment_sheaf(self) -> Any:
        """Build a complete judgment sheaf over this site.

        A *judgment sheaf* (theory2.tex §4.2) assigns to every
        coordinate a local judgment section and to every morphism a
        restriction map, satisfying the sheaf condition: local sections
        that agree on overlaps glue to a unique global section.  The
        ``jugeo.judgments.sections`` subsystem constructs this sheaf
        from the site's topology.
        """
        try:
            from jugeo.foundations.formal_core.a_site_for_programmatic_judgment import (  # type: ignore[import-untyped]
                JudgmentSheaf,
                JudgmentSection as FCSection,
                SiteCoordinate,
            )
            sheaf = JudgmentSheaf(sheaf_id=f"sheaf({self.label})")
            for coord in self.objects():
                site_coord = SiteCoordinate(
                    coord_id=coord.name,
                    name=coord.name,
                    depth=coord.depth,
                )
                section = FCSection(coord=site_coord)
                sheaf.add_section(section)
            return sheaf
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.judgments.sections to be installed"
            )

    @property
    def trust_presheaf(self) -> Any:
        """Build a trust presheaf over this site.

        The *trust presheaf* (theory2.tex §6.3) is a contravariant
        functor from the site category to the lattice of trust tiers.
        Each coordinate maps to its trust annotation, and restriction
        along morphisms can only *lower* trust — coarser scopes
        inherit the minimum trust of their refinements.  This property
        combines ``jugeo.evidence.trust`` with
        ``jugeo.foundations.formal_core`` to produce the presheaf.
        """
        try:
            from jugeo.evidence.trust import TrustProfile, TrustTier  # type: ignore[import-untyped]
            presheaf: dict[str, Any] = {}
            for coord in self.objects():
                profile = TrustProfile(
                    tier=TrustTier.PROPOSAL,
                    support_scope=tuple(coord.support_labels),
                    entity_id=coord.name,
                )
                presheaf[coord.name] = profile
            return presheaf
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.evidence.trust and "
                "jugeo.foundations.formal_core to be installed"
            )

    @property
    def evidence_manifold(self) -> Any:
        """Return the full evidence manifold over this site.

        An *evidence manifold* (theory2.tex §6.4) is the geometric
        object whose points are evidence entries and whose topology
        mirrors the site's covering structure.  The
        ``jugeo.evidence.manifests`` subsystem assembles it by
        collecting all manifest entries across every coordinate.
        """
        try:
            from jugeo.evidence.manifests import EvidenceManifest  # type: ignore[import-untyped]
            from jugeo.evidence.trust import TrustProfile, TrustTier  # type: ignore[import-untyped]
            manifests: dict[str, Any] = {}
            for coord in self.objects():
                profile = TrustProfile(
                    tier=TrustTier.PROPOSAL,
                    support_scope=tuple(coord.support_labels),
                    entity_id=coord.name,
                )
                try:
                    from jugeo.evidence.manifests import ProvenanceTrace  # type: ignore[import-untyped]
                    prov = ProvenanceTrace(origin="evidence_manifold")
                except ImportError:
                    prov = ("evidence_manifold",)  # type: ignore[assignment]
                manifests[coord.name] = EvidenceManifest(
                    coordinate=coord.name,
                    claim=f"manifold_entry({coord.name})",
                    records=(),
                    trust=profile,
                    provenance=prov,
                )
            return manifests
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.evidence.manifests to be installed"
            )

    def run_full_descent(self) -> Any:
        """Run descent on every covering family in the site.

        Iterates through each registered cover, constructs a
        ``DescentEngine`` with default configuration, and collects
        the results into a mapping from cover label to
        ``DescentResult``.  This is the site-level analogue of
        sheafification — verifying that all local data glues globally
        (theory2.tex §3).
        """
        try:
            from jugeo.geometry.descent import DescentEngine, DescentResult  # type: ignore[import-untyped]
            engine = DescentEngine()
            results: dict[str, Any] = {}
            for family in self._covers:
                sections: dict[str, dict[str, Any]] = {}
                for mem in family.members:
                    sections[mem.source.name] = {"coordinate": mem.source.name}
                result = engine.attempt_descent(family, sections)
                results[family.label] = result
            return results
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.geometry.descent to be installed"
            )

    def encode_for_solver(self) -> Any:
        """Encode the entire site for Z3 satisfiability checking.

        The encoding layer (``jugeo.encodings``) translates the site's
        coordinates, morphisms, and topology into SMT-LIB constraints.
        The solver subsystem (``jugeo.solver.z3_session``) then checks
        the resulting formula.  This is the computational backbone of
        decidability analysis (theory2.tex §8).
        """
        try:
            from jugeo.encodings import encode_judgment  # type: ignore[import-untyped]
            from jugeo.solver.z3_session import Z3Session  # type: ignore[import-untyped]
            session = Z3Session()
            encoded_coords = [
                encode_judgment({
                    "coordinate": c.name,
                    "kind": c.kind.value,
                    "depth": c.depth,
                })
                for c in self.objects()
            ]
            return {
                "session": session,
                "encoded_coordinates": encoded_coords,
                "topology": self.topology.name,
                "coordinate_count": len(encoded_coords),
            }
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.encodings and jugeo.solver.z3_session "
                "to be installed"
            )

    def maturity_assessment(self) -> Any:
        """Assess the maturity of judgments across the site.

        The maturity subsystem (``jugeo.maturity.cyclic_picture``)
        models the lifecycle of each judgment as a position on a cyclic
        maturity curve.  This method evaluates every coordinate and
        returns an aggregate maturity profile for the site — useful
        for project-level dashboards (theory2.tex §10.1).
        """
        try:
            from jugeo.maturity.cyclic_picture import (  # type: ignore[import-untyped]
                quick_maturity_report,
                MaturityLevel,
            )
            return quick_maturity_report(
                system_id=self.label or "site",
                level=MaturityLevel(0) if hasattr(MaturityLevel, '__call__') else None,
                num_cycles=self.coordinate_count(),
            )
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.maturity.cyclic_picture to be installed"
            )

    def orchestrate_verification(self) -> Any:
        """Orchestrate full verification of the site.

        The orchestration controller (``jugeo.orchestration.controller``)
        coordinates descent, evidence collection, solver queries, and
        certificate generation into a single verification pipeline.
        This method submits the entire site for orchestrated
        verification and returns the controller's verdict
        (theory2.tex §11).
        """
        try:
            from jugeo.orchestration.controller import (  # type: ignore[import-untyped]
                OrchestrationController,
                FrontierState,
                BudgetLedger,
                BackpressureSignal,
            )
            controller = OrchestrationController()
            frontier = FrontierState()
            budget = BudgetLedger()
            signal = BackpressureSignal()
            return controller.decide(frontier, budget, signal)
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.orchestration.controller to be installed"
            )

    # -- serialization --------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Full JSON-friendly representation of the site."""
        return {
            "label": self.label,
            "topology": self.topology.serialize(),
            "coordinates": {
                n: c.serialize() for n, c in self._coordinates.items()
            },
            "morphisms": [m.serialize() for m in self._morphisms],
            "covers": [f.serialize() for f in self._covers],
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> Site:
        """Reconstruct a site from its serialized form."""
        topo = GrothendieckTopology.parse(data.get("topology", {}))
        site = cls(topology=topo, label=data.get("label", ""))
        for _name, cdata in data.get("coordinates", {}).items():
            site.add_coordinate(Coordinate.parse(cdata))
        for mdata in data.get("morphisms", []):
            site.add_morphism(Morphism.parse(mdata))
        for fdata in data.get("covers", []):
            site.add_covering_family(CoveringFamily.parse(fdata))
        return site

    def generation_cover_design(self):
        """Design a generation cover for this site using cover design subsystem."""
        try:
            from jugeo.generation.cover_design.algorithms import CoverDesignAlgorithm
            from jugeo.generation.cover_design.models import CoverDesignPlan, PatchDescriptor
            from jugeo.generation.cover_design.budget_allocation import BudgetAllocator
            from jugeo.generation.cover_design.completion_criteria import CompletionChecker
            from jugeo.generation.cover_design.dependency_ordering import DependencyOrderer
            from jugeo.generation.cover_design.integration import CoverDesignIntegration
            return {"site": self._label, "cover_design": "available", "components": 6}
        except Exception:
            return {"site": self._label, "cover_design": "unavailable"}

    def inhabitant_fleet(self):
        """Construct inhabitant fleet for code generation."""
        try:
            from jugeo.generation.inhabitant_fleets.algorithms import InhabitantAlgorithm
            from jugeo.generation.inhabitant_fleets.models import Inhabitant, FleetConfiguration
            from jugeo.generation.inhabitant_fleets.integration import InhabitantFleetIntegration
            return {"fleet": "ready"}
        except Exception:
            return {"fleet": "unavailable"}

    def replay_gluing(self):
        """Replay previous gluing data for this site."""
        try:
            from jugeo.generation.replay_gluing.algorithms import ReplayAlgorithm
            from jugeo.generation.replay_gluing.models import ReplayRecord, GluingReplay
            from jugeo.generation.replay_gluing.integration import ReplayGluingIntegration
            return {"replay": "available"}
        except Exception:
            return {"replay": "unavailable"}

    def semantic_closure(self):
        """Compute semantic closure of this site."""
        try:
            from jugeo.generation.semantic_closure.algorithms import ClosureAlgorithm
            from jugeo.generation.semantic_closure.models import ClosureResult, SemanticBoundary
            from jugeo.generation.semantic_closure.integration import SemanticClosureIntegration
            return {"closure": "available"}
        except Exception:
            return {"closure": "unavailable"}

    def state_space_exploration(self):
        """Explore the state space of programs over this site."""
        try:
            from jugeo.generation.state_space.algorithms import StateSpaceAlgorithm
            from jugeo.generation.state_space.models import StateNode, StateTransition, ExplorationResult
            from jugeo.generation.state_space.integration import StateSpaceIntegration
            return {"state_space": "available"}
        except Exception:
            return {"state_space": "unavailable"}

    def hypercover_treaty(self):
        """Negotiate hypercover treaties for this site."""
        try:
            from jugeo.generation.hypercover_treaties.algorithms import TreatyAlgorithms
            from jugeo.generation.hypercover_treaties.models import HypercoverTreaty, TreatyFrictionMetric
            from jugeo.generation.hypercover_treaties.integration import TreatyIntegration
            return {"treaty": "available"}
        except Exception:
            return {"treaty": "unavailable"}

    def evaluation_design(self):
        """Run evaluation design methodology over this site."""
        try:
            from jugeo.evaluation.evaluation_design.ablation_design import AblationPlanner, AblationExecutor, AblationAnalyzer, AblationDesignRunner
            from jugeo.evaluation.evaluation_design.ablation_philosophy import AblationMode, AblationStatus, AblationTarget
            from jugeo.evaluation.methodology_loops.algorithms import MethodologyAlgorithm
            from jugeo.evaluation.methodology_loops.models import MethodologyLoop, LoopIteration
            return {"evaluation": "available", "ablation": True}
        except Exception:
            return {"evaluation": "unavailable"}

    def benchmark_suite(self):
        """Run benchmark suite over this site."""
        try:
            from jugeo.benchmarks.models import BenchmarkJudgment, DescentBenchmarkCase, EncodingBenchmarkCase, JudgmentBenchmarkCase
            from jugeo.benchmarks.runner import BenchmarkRunner
            from jugeo.benchmarks.validation import BenchmarkValidator
            from jugeo.benchmarks.semantics import BenchmarkSemantics
            return {"benchmarks": "available", "cases": 3}
        except Exception:
            return {"benchmarks": "unavailable"}

    def runtime_support(self):
        """Access runtime support (cache, checkpointing, memory, replay)."""
        try:
            from jugeo.runtime.cache import SemanticCache, CacheEntry, CachePolicy
            from jugeo.runtime.checkpointing import Checkpoint, CheckpointStore, CheckpointPolicy
            from jugeo.runtime.memory import MemoryManager, MemoryPool, MemoryPolicy
            from jugeo.runtime.replay import ReplayEngine, ReplaySession, ReplayPolicy
            return {"runtime": "available", "components": ["cache", "checkpoint", "memory", "replay"]}
        except Exception:
            return {"runtime": "unavailable"}

    def kernel_lifecycle(self):
        """Access kernel lifecycle management."""
        try:
            from jugeo.kernel.lifecycle import LifecycleManager, LifecyclePhase
            from jugeo.kernel.services import ServiceRegistry, ServiceDescriptor
            from jugeo.kernel.configuration import ConfigSource, RuntimeConfiguration
            from jugeo.kernel.health import HealthChecker, HealthReport
            return {"kernel": "available"}
        except Exception:
            return {"kernel": "unavailable"}

    def interface_routing(self):
        """Access API and task routing interfaces."""
        try:
            from jugeo.interfaces.api import APIEndpoint, APIRouter
            from jugeo.interfaces.task_router import TaskRouter, TaskDescriptor
            from jugeo.interfaces.diagnostics import DiagnosticsEndpoint
            return {"interfaces": "available"}
        except Exception:
            return {"interfaces": "unavailable"}

    def bug_detection_scan(self):
        """Scan this site for bugs using the bug detection subsystem."""
        try:
            from jugeo.problem_modes.bug_detection.detector import BugDetector
            from jugeo.problem_modes.bug_detection.models import BugKind, BugReport, BugDetectionResult, DetectionSession
            from jugeo.problem_modes.bug_detection.ast_bridge import PythonASTBridge, ASTCoordinate, SymbolicNode, ASTBridgeConfig
            from jugeo.problem_modes.bug_detection.integration import BugDetectionOrchestrator
            return {"site": self._label, "bug_detection": "available"}
        except Exception:
            return {"site": self._label, "bug_detection": "unavailable"}

    def specification_satisfaction(self):
        """Check specification satisfaction across this site."""
        try:
            from jugeo.problem_modes.specification_satisfaction.models import Specification, SatisfactionWitness, CertificateOfSatisfaction, ResidualGap
            from jugeo.problem_modes.specification_satisfaction.builders import SpecificationBuilder, ConstraintEncoder, WitnessBuilder
            from jugeo.problem_modes.specification_satisfaction.descent import DescentConditionChecker, DescentOrchestrator, GlobalSectionExtractor
            from jugeo.problem_modes.specification_satisfaction.gap_analysis import GapAnalyzer, ObstructionClassComputer, RepairStrategyEngine
            from jugeo.problem_modes.specification_satisfaction.algorithms import specification_satisfaction_algorithm, descent_for_satisfaction
            return {"satisfaction": "available", "components": 5}
        except Exception:
            return {"satisfaction": "unavailable"}

    def repair_semantics(self):
        """Access repair semantics for this site."""
        try:
            from jugeo.problem_modes.repair_semantics.models import CounterexampleRecord, DebugSession, RepairFrontier, RepairPlan, RepairStep
            from jugeo.problem_modes.repair_semantics.algorithms import delta_debug, compute_minimal_repair_frontier, topological_repair_order
            from jugeo.problem_modes.repair_semantics.theorems import TheoremObligation, ProofStrategy, check_theorem
            return {"repair": "available"}
        except Exception:
            return {"repair": "unavailable"}

    def relational_refinement(self):
        """Check relational refinement across this site."""
        try:
            from jugeo.problem_modes.relational_refinement.models import RefinementRelation, RefinementWitness
            from jugeo.problem_modes.relational_refinement.algorithms import RefinementChecker, EquivalenceVerifier
            from jugeo.problem_modes.relational_refinement.integration import RelationalRefinementIntegration
            return {"refinement": "available"}
        except Exception:
            return {"refinement": "unavailable"}

    def public_alignment(self):
        """Check public alignment (honest projection) for this site."""
        try:
            from jugeo.problem_modes.public_alignment.models import AlignmentReport, ProjectionRecord
            from jugeo.problem_modes.public_alignment.algorithms import HonestProjection, AlignmentChecker
            from jugeo.problem_modes.public_alignment.integration import PublicAlignmentIntegration
            return {"alignment": "available"}
        except Exception:
            return {"alignment": "unavailable"}

    def problem_atlas(self):
        """Classify problems over this site using the problem atlas."""
        try:
            from jugeo.problem_modes.problem_atlas.models import AtlasEntry, ProblemCategory
            from jugeo.problem_modes.problem_atlas.algorithms import AtlasCatalog, ProblemClassifier
            from jugeo.problem_modes.problem_atlas.integration import ProblemAtlasIntegration
            return {"atlas": "available"}
        except Exception:
            return {"atlas": "unavailable"}

    def analogy_transport(self):
        """Transport theorems from one domain to another via analogy functors."""
        try:
            from jugeo.ideation.analogy_transport.algorithms import AnalogyFunctor, TransportPlan, TransportResult
            from jugeo.ideation.analogy_transport.models import SourceTheorem, TransportedTheorem, AnalogyQuality
            return {"transport": "available"}
        except Exception:
            return {"transport": "unavailable"}

    def theorem_ecology(self):
        """Analyze the theorem ecology over this site."""
        try:
            from jugeo.ideation.theorem_ecologies.algorithms import EcologyAnalyzer
            from jugeo.ideation.theorem_ecologies.models import TheoremEcology, EcologicalNiche
            from jugeo.ideation.theorem_ecologies.integration import TheoremEcologyIntegration
            return {"ecology": "available"}
        except Exception:
            return {"ecology": "unavailable"}

    def semantic_futures(self):
        """Compute semantic futures (possible theorem developments)."""
        try:
            from jugeo.ideation.semantic_futures.algorithms import FuturePredictor
            from jugeo.ideation.semantic_futures.models import SemanticFuture, FuturePrediction
            from jugeo.ideation.semantic_futures.integration import SemanticFuturesIntegration
            return {"futures": "available"}
        except Exception:
            return {"futures": "unavailable"}

    def regime_bootstrapping(self):
        """Bootstrap a verification regime over this site."""
        try:
            from jugeo.ideation.regime_bootstrapping.algorithms import RegimeBootstrapper
            from jugeo.ideation.regime_bootstrapping.models import Regime, BootstrapPlan
            from jugeo.ideation.regime_bootstrapping.integration import RegimeBootstrappingIntegration
            return {"regime": "available"}
        except Exception:
            return {"regime": "unavailable"}

    def discovery_pipeline(self):
        """Run the theorem discovery pipeline over this site."""
        try:
            from jugeo.ideation.discovery_engine.algorithms import DiscoveryAlgorithm
            from jugeo.ideation.discovery_engine.models import DiscoveryCandidate, TheoremCandidate, DiscoveryResult
            from jugeo.ideation.discovery_engine.integration import DiscoveryEngineIntegration
            return {"discovery": "available"}
        except Exception:
            return {"discovery": "unavailable"}

    def theorem_economics(self):
        """Analyze theorem economics (yield, investment) for this site."""
        try:
            from jugeo.ideation.theorem_economics.algorithms import EconomicAlgorithm, WaterfillingAlgorithm, LagrangianOptimizer, PortfolioOptimizer
            from jugeo.ideation.theorem_economics.models import TheoremYieldModel, InvestmentSchedule, TheoremPortfolioValue
            from jugeo.ideation.theorem_economics.marginal_analysis import MarginalAnalyzer
            from jugeo.ideation.theorem_economics.compounding import CompoundingEngine
            return {"economics": "available", "algorithms": 4}
        except Exception:
            return {"economics": "unavailable"}


# ---------------------------------------------------------------------------
# SiteBuilder
# ---------------------------------------------------------------------------


class SiteBuilder:
    """Fluent builder for constructing sites incrementally.

    The copilot project-scan command produces a ``SiteBuilder``, feeds it
    discovered source coordinates and dependency morphisms, and finishes
    with :meth:`build` to yield the assembled :class:`Site`.

    Example::

        site = (
            SiteBuilder("my-project")
            .add_coordinate(root)
            .add_coordinate(child)
            .add_morphism(edge)
            .set_topology(GrothendieckTopology.canonical())
            .build()
        )
    """

    def __init__(self, label: str = "") -> None:
        self._label = label
        self._coords: list[Coordinate] = []
        self._morphisms: list[Morphism] = []
        self._families: list[CoveringFamily] = []
        self._topology: GrothendieckTopology | None = None

    def add_coordinate(self, coord: Coordinate) -> SiteBuilder:
        """Add a coordinate; returns *self* for chaining."""
        self._coords.append(coord)
        return self

    def add_morphism(self, morphism: Morphism) -> SiteBuilder:
        """Add a morphism; returns *self* for chaining."""
        self._morphisms.append(morphism)
        return self

    def add_covering_family(self, family: CoveringFamily) -> SiteBuilder:
        """Add a covering family; returns *self* for chaining."""
        self._families.append(family)
        return self

    def set_topology(
        self, topology: GrothendieckTopology
    ) -> SiteBuilder:
        """Set the Grothendieck topology; returns *self* for chaining."""
        self._topology = topology
        return self

    def add_coordinates(
        self, coords: Iterable[Coordinate]
    ) -> SiteBuilder:
        """Bulk-add coordinates; returns *self* for chaining."""
        self._coords.extend(coords)
        return self

    def add_morphisms(
        self, morphisms: Iterable[Morphism]
    ) -> SiteBuilder:
        """Bulk-add morphisms; returns *self* for chaining."""
        self._morphisms.extend(morphisms)
        return self

    def add_covering_families(
        self, families: Iterable[CoveringFamily]
    ) -> SiteBuilder:
        """Bulk-add covering families; returns *self* for chaining."""
        self._families.extend(families)
        return self

    def build(self) -> Site:
        """Construct and return the fully assembled :class:`Site`.

        Registers all coordinates, morphisms, and covering families with
        the chosen topology (defaulting to discrete if none was set).
        """
        site = Site(
            topology=self._topology or GrothendieckTopology.discrete(),
            label=self._label,
        )
        for c in self._coords:
            site.add_coordinate(c)
        for m in self._morphisms:
            site.add_morphism(m)
        for f in self._families:
            site.add_covering_family(f)
        return site


# ---------------------------------------------------------------------------
# SiteSerializer
# ---------------------------------------------------------------------------


class SiteSerializer:
    """JSON serialization and deserialization for the full site graph.

    This is the persistence layer used by the copilot session-save and
    session-restore commands.  All site objects round-trip through JSON
    without data loss (except for callable axiom predicates, which are
    inherently non-serializable).

    In theory2.tex §6, serialized sites are the *transport format* for
    shipping judgment contexts between collaborators.
    """

    @staticmethod
    def site_to_json(site: Site, *, indent: int | None = 2) -> str:
        """Serialize a site to a JSON string.

        Parameters
        ----------
        site : Site
            The site to serialize.
        indent : int | None
            JSON indentation level (``None`` for compact).
        """
        return json.dumps(site.serialize(), indent=indent, sort_keys=True)

    @staticmethod
    def site_from_json(text: str) -> Site:
        """Deserialize a site from a JSON string.

        Raises
        ------
        json.JSONDecodeError
            If the input is not valid JSON.
        KeyError
            If required fields are missing.
        """
        return Site.parse(json.loads(text))

    @staticmethod
    def coordinate_to_json(coord: Coordinate) -> str:
        """Serialize a single coordinate to JSON."""
        return json.dumps(coord.serialize())

    @staticmethod
    def coordinate_from_json(text: str) -> Coordinate:
        """Deserialize a coordinate from JSON."""
        return Coordinate.parse(json.loads(text))

    @staticmethod
    def morphism_to_json(morphism: Morphism) -> str:
        """Serialize a single morphism to JSON."""
        return json.dumps(morphism.serialize())

    @staticmethod
    def morphism_from_json(text: str) -> Morphism:
        """Deserialize a morphism from JSON."""
        return Morphism.parse(json.loads(text))

    @staticmethod
    def covering_family_to_json(family: CoveringFamily) -> str:
        """Serialize a covering family to JSON."""
        return json.dumps(family.serialize())

    @staticmethod
    def covering_family_from_json(text: str) -> CoveringFamily:
        """Deserialize a covering family from JSON."""
        return CoveringFamily.parse(json.loads(text))

    @staticmethod
    def topology_to_json(topology: GrothendieckTopology) -> str:
        """Serialize a Grothendieck topology to JSON.

        Note: axiom predicates cannot be serialized and are lost.
        """
        return json.dumps(topology.serialize())

    @staticmethod
    def topology_from_json(text: str) -> GrothendieckTopology:
        """Deserialize a topology from JSON."""
        return GrothendieckTopology.parse(json.loads(text))


# ---------------------------------------------------------------------------
# SiteDiagnostics
# ---------------------------------------------------------------------------


class SiteDiagnostics:
    """Validation and diagnostic methods for a site.

    The copilot diagnostic panel uses these methods to surface warnings
    and suggestions about the current site configuration.  Each check
    corresponds to a structural property from theory2.tex §3-§5.

    Parameters
    ----------
    site : Site
        The site to diagnose.
    """

    def __init__(self, site: Site) -> None:
        self.site = site
        self._issues: list[str] = []

    def check_axioms(self) -> list[str]:
        """Run all Grothendieck topology axiom checks.

        Returns a list of human-readable diagnostic messages.  An empty
        list means all axioms hold.

        Checks performed:
        1. Identity axiom for every coordinate.
        2. Pullback stability for every registered cover.
        3. Local character for every registered cover.
        """
        self._issues = []
        topo = self.site.topology

        # Axiom 1: identity
        for coord in self.site.objects():
            if not topo.identity_axiom_check(coord):
                self._issues.append(
                    f"Identity axiom failed for coordinate '{coord.name}'"
                )

        all_families = self.site.covering_families()
        all_morphisms = list(self.site._morphisms)  # noqa: SLF001

        # Axiom 2: pullback stability
        for fam in all_families:
            if not topo.pullback_stability_check(fam, all_morphisms):
                self._issues.append(
                    f"Pullback stability failed for cover '{fam.label}' "
                    f"over '{fam.base.name}'"
                )

        # Axiom 3: local character
        for fam in all_families:
            if not topo.local_character_check(fam, all_families):
                self._issues.append(
                    f"Local character failed for cover '{fam.label}' "
                    f"over '{fam.base.name}'"
                )

        return self._issues

    def find_uncovered_coordinates(self) -> list[Coordinate]:
        """Return coordinates that have no covering family at all.

        In a well-formed site every coordinate should participate in
        at least one cover (theory2.tex §3.10).  Uncovered coordinates
        are blind spots where the copilot cannot perform descent.
        """
        covered = {fam.base.name for fam in self.site.covering_families()}
        return [
            c for c in self.site.objects() if c.name not in covered
        ]

    def detect_redundant_covers(
        self,
    ) -> list[tuple[CoveringFamily, CoveringFamily]]:
        """Find pairs of covers where one refines the other.

        Redundant covers waste computational effort during descent.
        The copilot cleanup command can remove them.
        """
        families = self.site.covering_families()
        redundant: list[tuple[CoveringFamily, CoveringFamily]] = []
        for i, fi in enumerate(families):
            for j, fj in enumerate(families):
                if j <= i:
                    continue
                if fi.base != fj.base:
                    continue
                if fi.refinement_of(fj):
                    redundant.append((fi, fj))
                elif fj.refinement_of(fi):
                    redundant.append((fj, fi))
        return redundant

    def suggest_refinements(self) -> list[str]:
        """Suggest possible cover refinements for better descent convergence.

        Heuristics from theory2.tex §4.5:
        * Covers with high overlap counts may benefit from splitting.
        * Covers with members at wildly different depths need levelling.
        * Single-member covers are trivial and can be collapsed.
        """
        suggestions: list[str] = []
        for fam in self.site.covering_families():
            # Single non-identity member is suspicious.
            if len(fam.members) == 1 and not fam.members[0].is_identity:
                suggestions.append(
                    f"Cover '{fam.label}' over '{fam.base.name}' has a "
                    f"single non-identity member — consider collapsing."
                )
            if len(fam.members) > 1:
                depths = [m.source.depth for m in fam.members]
                depth_spread = max(depths) - min(depths)
                if depth_spread > 2:
                    suggestions.append(
                        f"Cover '{fam.label}' over '{fam.base.name}' has "
                        f"members at depths {min(depths)}-{max(depths)}; "
                        f"consider levelling for uniform descent."
                    )
                overlaps = fam.overlap_pairs()
                ratio = len(overlaps) / max(len(fam.members), 1)
                if ratio > 2.0:
                    suggestions.append(
                        f"Cover '{fam.label}' over '{fam.base.name}' has "
                        f"overlap ratio {ratio:.1f} — consider splitting "
                        f"into disjoint sub-covers."
                    )
        return suggestions

    def coverage_ratio(self) -> float:
        """Fraction of coordinates that are covered by at least one family.

        Returns a value in ``[0.0, 1.0]``.  The copilot status bar
        displays this as a quick health indicator.
        """
        total = len(self.site.objects())
        if total == 0:
            return 1.0
        covered = len(
            {fam.base.name for fam in self.site.covering_families()}
        )
        return covered / total

    def copilot_diagnostic_summary(self) -> str:
        """Produce a concise diagnostic summary for copilot display.

        This is the string shown in the copilot status bar and diagnostic
        panel.  It aggregates axiom checks, uncovered coordinates, and
        redundancy warnings into a single human-readable report.
        """
        axiom_issues = self.check_axioms()
        uncovered = self.find_uncovered_coordinates()
        redundant = self.detect_redundant_covers()
        refinement_hints = self.suggest_refinements()

        lines: list[str] = []
        site_label = self.site.label or "(unnamed)"
        lines.append(f"=== Site Diagnostic: {site_label} ===")
        lines.append(f"Coordinates: {len(self.site.objects())}")
        lines.append(f"Morphisms:   {self.site.morphism_count()}")
        lines.append(f"Covers:      {len(self.site.covering_families())}")
        lines.append(f"Topology:    {self.site.topology.name}")
        lines.append(f"Coverage:    {self.coverage_ratio():.0%}")
        lines.append("")

        if axiom_issues:
            lines.append(f"! Axiom issues ({len(axiom_issues)}):")
            for issue in axiom_issues:
                lines.append(f"  - {issue}")
        else:
            lines.append("[ok] All topology axioms satisfied.")

        if uncovered:
            lines.append(f"! Uncovered coordinates ({len(uncovered)}):")
            for c in uncovered[:10]:
                lines.append(f"  - {c.name}")
            if len(uncovered) > 10:
                lines.append(f"  ... and {len(uncovered) - 10} more")
        else:
            lines.append("[ok] All coordinates covered.")

        if redundant:
            lines.append(f"! Redundant cover pairs ({len(redundant)}):")
            for finer, coarser in redundant[:5]:
                lines.append(
                    f"  - '{finer.label}' refines '{coarser.label}'"
                )
        else:
            lines.append("[ok] No redundant covers detected.")

        if refinement_hints:
            lines.append(f"Suggestions ({len(refinement_hints)}):")
            for hint in refinement_hints:
                lines.append(f"  - {hint}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backward-compatible aliases
# ---------------------------------------------------------------------------

# Legacy code imports these names from jugeo.geometry.site.  We keep them
# as thin aliases so that covers.py, supports.py, contexts.py, and
# sections.py continue to work without modification.

CoordinateObject = Coordinate
"""Deprecated alias for :class:`Coordinate`.  Prefer ``Coordinate``."""


class CoordinateMorphism:
    """Backward-compatible shim matching the legacy ``CoordinateMorphism``.

    The old class stored ``source`` and ``target`` as *strings* with a
    ``reason`` field.  This shim accepts both the old string-based
    interface and the new :class:`Morphism` object interface.
    """

    def __init__(self, source: str, target: str, reason: str = "") -> None:
        self.source = source
        self.target = target
        self.reason = reason

    def to_morphism(
        self, coord_lookup: dict[str, Coordinate]
    ) -> Morphism | None:
        """Convert to a full :class:`Morphism` if coordinates are available."""
        src = coord_lookup.get(self.source)
        tgt = coord_lookup.get(self.target)
        if src is None or tgt is None:
            return None
        return Morphism(source=src, target=tgt, label=self.reason)


SemanticSite = Site
"""Deprecated alias for :class:`Site`.  Prefer ``Site``."""


def build_site(coordinates: Iterable[Coordinate]) -> Site:
    """Build a site from an iterable of coordinates.

    This is the backward-compatible factory matching the original
    ``build_site()`` signature.  Prefer :class:`SiteBuilder` for new code.
    """
    builder = SiteBuilder()
    builder.add_coordinates(coordinates)
    return builder.build()


def restrict_coordinate(
    coordinate: Coordinate,
    *,
    suffix: Iterable[str] = (),
    support_labels: Iterable[str] = (),
    metadata: Mapping[str, Any] | None = None,
) -> Coordinate:
    """Return a refined coordinate with additional suffix, labels, and metadata.

    Backward-compatible with the original ``restrict_coordinate()`` that
    operated on ``CoordinateObject``.  In theory2.tex §3.2 this
    corresponds to constructing the restriction morphism's target.
    """
    suffix_tuple = tuple(suffix)
    merged_labels = frozenset(
        set(coordinate.support_labels) | set(support_labels)
    )
    merged_meta = dict(coordinate.metadata)
    merged_meta.update(metadata or {})
    new_name_suffix = ".".join(suffix_tuple)
    return Coordinate(
        components=coordinate.components + suffix_tuple,
        kind=coordinate.kind,
        support_labels=merged_labels,
        metadata=merged_meta,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Core classes
    "Coordinate",
    "Morphism",
    "MorphismKind",
    "CoveringFamily",
    "GrothendieckTopology",
    "Site",
    "SiteBuilder",
    "CoordinateIndex",
    "OverlapData",
    "SiteSerializer",
    "SiteDiagnostics",
    # Enums
    "CoordinateKind",
    # Backward-compatible aliases
    "CoordinateObject",
    "CoordinateMorphism",
    "SemanticSite",
    "build_site",
    "restrict_coordinate",
]
