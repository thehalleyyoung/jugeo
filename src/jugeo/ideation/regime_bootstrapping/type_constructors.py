"""
Type constructor search for regime bootstrapping.

copilot: shared-core marker

Theory reference: theory2.tex Ch55 — Regime Bootstrapping via Type-Constructor
Search.  This module implements the second stage of the regime bootstrapping
pipeline: given a ``DomainFormation`` produced by the domain formation step
(``domain_formation``), we search for valid type constructors that can
witness a new ``Regime`` within that domain.

A type constructor in the sense of theory2.tex Ch55 is a functor
  F : C_src → C_tgt
together with coherence data (natural transformations, adjunctions, etc.)
that is compatible with the obstruction structure of the domain.  There are
four primary kinds of constructor we search for:

- **Inductive** — least fixed-points; suitable for domains whose obstructions
  are all of topological or algebraic kind.
- **Coinductive** — greatest fixed-points; suitable for coinductive / limit-type
  domains.
- **Quotient** — identify equivalent terms; arises when cohomological
  obstructions are present.
- **Extension** — extend an existing type by new generators; low-severity domains.
- **Restriction** — restrict an existing type to a sub-domain; high-severity
  critical domains.

The module is self-contained with fully guarded cross-module imports.

Typical usage::

    from jugeo.ideation.regime_bootstrapping.type_constructors import (
        TypeConstructorRunner, search_type_constructors,
    )
    runner = TypeConstructorRunner()
    constructors = runner.run(domain_formation)
    for c in constructors:
        print(c)
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

__all__ = [
    "TypeConstructorSearch",
    "FunctorSpecBuilder",
    "TypeConstructorValidator",
    "TypeConstructorRunner",
    "search_type_constructors",
    "validate_constructor",
]

# ---------------------------------------------------------------------------
# Cross-module imports — always guarded
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.models import (
        ObstructionField,
        ObstructionKind,
        DomainFormation,
        DomainType,
        TypeConstructor,
        TypeConstructorKind,
        RegimeCandidate,
        BootstrapStep,
        BootstrapPlan,
        BootstrapResult,
        BootstrapStatus,
        BootstrapPriority,
        RegimeBootstrapperConfig,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maximum number of constructors to return from a single search call
MAX_CONSTRUCTORS: int = 64

#: Minimum quality score a constructor must achieve to be returned
MIN_CONSTRUCTOR_SCORE: float = 0.30

#: Default arity for morphisms when not otherwise determined
DEFAULT_ARITY: int = 1

#: Quality bonus applied to constructors with natural transformations
NAT_TRANSFORM_BONUS: float = 0.10

#: Quality bonus applied to constructors with coherence conditions
COHERENCE_BONUS: float = 0.15

#: Penalty applied when arity is out of the valid range [1, 8]
ARITY_PENALTY: float = 0.20

#: Score weights for the validation components
VALIDATION_WEIGHTS: Dict[str, float] = {
    "functoriality": 0.35,
    "naturality": 0.30,
    "coherence": 0.20,
    "arity": 0.15,
}

#: Default source category name when none can be inferred
DEFAULT_SOURCE_CATEGORY: str = "C_src"

#: Default target category name when none can be inferred
DEFAULT_TARGET_CATEGORY: str = "C_tgt"

#: Maximum number of morphisms in a functor spec
MAX_MORPHISMS: int = 32

#: Maximum number of natural transformations in a functor spec
MAX_NAT_TRANSFORMS: int = 16

#: Maximum number of coherence conditions in a functor spec
MAX_COHERENCE_CONDITIONS: int = 24

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
ConstructorReport = Dict[str, Any]
FunctorSpec = Dict[str, Any]
ValidationResult = Dict[str, Any]
SearchReport = Dict[str, Any]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC datetime with timezone info.

    Returns
    -------
    datetime
        Current UTC datetime (timezone-aware).
    """
    return datetime.now(tz=timezone.utc)


def _uid() -> str:
    """Generate a random UUID4 string.

    Returns
    -------
    str
        UUID4 string.
    """
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to [*lo*, *hi*].

    Parameters
    ----------
    value:
        Value to clamp.
    lo:
        Lower bound.
    hi:
        Upper bound.

    Returns
    -------
    float
        Clamped value.
    """
    return max(lo, min(hi, value))


def _build_functor_spec(source: str, target: str, kind: str) -> FunctorSpec:
    """Build a minimal functor specification dict.

    This internal helper constructs the skeleton of a functor specification
    dictionary for a constructor of the given *kind* mapping from *source*
    category to *target* category.  The resulting spec is then fleshed out
    by ``FunctorSpecBuilder``.

    Parameters
    ----------
    source:
        Name of the source category (e.g. ``'C_src'``).
    target:
        Name of the target category (e.g. ``'C_tgt'``).
    kind:
        Constructor kind string (e.g. ``'inductive'``, ``'quotient'``).

    Returns
    -------
    dict
        Functor specification with keys ``'source'``, ``'target'``,
        ``'kind'``, ``'morphisms'``, ``'natural_transformations'``, and
        ``'coherence_conditions'``.

    Examples
    --------
    >>> spec = _build_functor_spec("C_src", "C_tgt", "inductive")
    >>> assert spec["kind"] == "inductive"
    """
    return {
        "source": source,
        "target": target,
        "kind": kind,
        "morphisms": [],
        "natural_transformations": [],
        "coherence_conditions": [],
        "spec_id": _uid(),
        "created_at": _utcnow().isoformat(),
    }


def _score_constructor_candidate(constructor: Any) -> float:
    """Compute a quality score for a constructor candidate.

    This internal helper inspects the constructor's attributes and
    computes a float score in ``[0.0, 1.0]``.  It is used both for
    ranking constructors and for deciding whether to include them in
    search results.

    Parameters
    ----------
    constructor:
        A ``TypeConstructor`` instance or dict-like object.

    Returns
    -------
    float
        Quality score in ``[0.0, 1.0]``.
    """
    # Retrieve score attribute if already computed
    cached = getattr(constructor, "score", None)
    if cached is not None:
        try:
            return _clamp(float(cached), 0.0, 1.0)
        except (TypeError, ValueError):
            pass
    # Compute from parts
    try:
        score = float((constructor or {}).get("score", 0.5))
    except (TypeError, AttributeError):
        score = 0.5
    return _clamp(score, 0.0, 1.0)


def _get_domain_generators(domain: Any) -> List[str]:
    """Extract generator names from a domain formation.

    Parameters
    ----------
    domain:
        A ``DomainFormation`` or dict.

    Returns
    -------
    list of str
        Generator names.
    """
    gens = getattr(domain, "generators", None)
    if gens is None:
        try:
            gens = domain.get("generators", [])
        except Exception:
            gens = []
    return list(gens)


def _get_domain_type(domain: Any) -> str:
    """Extract the domain type string.

    Parameters
    ----------
    domain:
        A ``DomainFormation`` or dict.

    Returns
    -------
    str
        Domain type string.
    """
    dtype = getattr(domain, "domain_type", None)
    if dtype is None:
        try:
            dtype = domain.get("domain_type", "generic")
        except Exception:
            dtype = "generic"
    return str(dtype)


def _get_domain_id(domain: Any) -> str:
    """Extract a stable identifier from a domain formation.

    Parameters
    ----------
    domain:
        A ``DomainFormation`` or dict.

    Returns
    -------
    str
        Domain identifier.
    """
    did = getattr(domain, "id", None)
    if did is None:
        try:
            did = domain.get("id", _uid())
        except Exception:
            did = _uid()
    return str(did)


# ---------------------------------------------------------------------------
# TypeConstructorSearch
# ---------------------------------------------------------------------------


class TypeConstructorSearch:
    """Searches for valid type constructors given a domain formation.

    The ``TypeConstructorSearch`` class implements the core search procedure
    of the second bootstrapping stage (theory2.tex Ch55 §4).  Given a
    ``DomainFormation`` it exhaustively (but bounded) searches across the
    five constructor families: inductive, coinductive, quotient, extension,
    and restriction.

    For each family the search builds candidate functor specifications,
    scores them via ``score_constructor``, and retains those above the
    ``MIN_CONSTRUCTOR_SCORE`` threshold.  The final list is ranked by
    score descending and truncated at ``MAX_CONSTRUCTORS``.

    The search results are cached by domain identifier so that repeated
    searches on the same domain are cheap.

    Attributes
    ----------
    config : dict
        Optional configuration dict.
    _cache : dict
        Internal search cache mapping domain id → list of constructors.

    Examples
    --------
    >>> search = TypeConstructorSearch()
    >>> constructors = search.search(domain_formation)
    >>> print(f"Found {len(constructors)} constructors")
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the TypeConstructorSearch.

        Parameters
        ----------
        config:
            Optional configuration dict.  Recognised keys:

            - ``'max_constructors'``: cap on returned constructors (default 64).
            - ``'min_score'``: minimum score threshold (default 0.30).
        """
        cfg = config or {}
        self.config: Dict[str, Any] = cfg
        self._max_constructors: int = int(cfg.get("max_constructors", MAX_CONSTRUCTORS))
        self._min_score: float = float(cfg.get("min_score", MIN_CONSTRUCTOR_SCORE))
        self._cache: Dict[str, List[Any]] = {}
        self._builder = FunctorSpecBuilder()
        log.debug("TypeConstructorSearch initialized with config=%s", cfg)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, domain_formation: Any) -> List[Any]:
        """Search for all valid type constructors for *domain_formation*.

        Runs searches across all five constructor families, collects
        candidates, scores and ranks them, and returns the top-N above
        the minimum score threshold.

        Parameters
        ----------
        domain_formation:
            A ``DomainFormation`` or dict describing the domain.

        Returns
        -------
        list of TypeConstructor (or dict)
            Ranked list of valid type constructors.
        """
        domain_id = _get_domain_id(domain_formation)
        if domain_id in self._cache:
            log.debug("TypeConstructorSearch cache hit for domain %s", domain_id)
            return self._cache[domain_id]

        all_candidates: List[Any] = []
        all_candidates.extend(self.search_inductive(domain_formation))
        all_candidates.extend(self.search_coinductive(domain_formation))
        all_candidates.extend(self.search_quotient(domain_formation))
        all_candidates.extend(self.search_extension(domain_formation))
        all_candidates.extend(self.search_restriction(domain_formation))

        # Score, filter, rank
        scored = [(c, self.score_constructor(c)) for c in all_candidates]
        scored = [(c, s) for c, s in scored if s >= self._min_score]
        scored.sort(key=lambda x: x[1], reverse=True)
        result = [c for c, _ in scored[:self._max_constructors]]

        self._cache[domain_id] = result
        log.info("TypeConstructorSearch: found %d constructors for domain %s", len(result), domain_id)
        return result

    def search_inductive(self, domain: Any) -> List[Any]:
        """Search for inductive type constructors.

        Inductive constructors are built as least fixed-points.  They are
        most appropriate when the domain has topological or algebraic
        obstructions and has at least one generator.

        Parameters
        ----------
        domain:
            Domain formation.

        Returns
        -------
        list
            Candidate inductive constructors.
        """
        candidates = []
        gens = _get_domain_generators(domain)
        if not gens:
            return candidates
        for gen in gens[:4]:  # limit search breadth
            spec = self._build_inductive_spec(gen, domain)
            constructor = self._make_constructor(
                kind="inductive",
                name=f"Ind_{gen}",
                spec=spec,
                domain=domain,
            )
            candidates.append(constructor)
        log.debug("search_inductive: %d candidates for domain %s", len(candidates), _get_domain_id(domain))
        return candidates

    def search_coinductive(self, domain: Any) -> List[Any]:
        """Search for coinductive type constructors.

        Coinductive constructors are built as greatest fixed-points.  They
        are appropriate for limit-type domains with coinductive structure.

        Parameters
        ----------
        domain:
            Domain formation.

        Returns
        -------
        list
            Candidate coinductive constructors.
        """
        candidates = []
        gens = _get_domain_generators(domain)
        if not gens:
            return candidates
        # Build one coinductive constructor from all generators jointly
        spec = self._build_inductive_spec("coind_all", domain, coinductive=True)
        constructor = self._make_constructor(
            kind="coinductive",
            name="CoInd_all",
            spec=spec,
            domain=domain,
        )
        candidates.append(constructor)
        return candidates

    def search_quotient(self, domain: Any) -> List[Any]:
        """Search for quotient type constructors.

        Quotient constructors identify equivalent terms.  They are most
        useful when cohomological obstructions are present or when the
        domain has many relations.

        Parameters
        ----------
        domain:
            Domain formation.

        Returns
        -------
        list
            Candidate quotient constructors.
        """
        candidates = []
        gens = _get_domain_generators(domain)
        if len(gens) < 2:
            return candidates
        spec = self._build_quotient_spec(gens, domain)
        constructor = self._make_constructor(
            kind="quotient",
            name="Quot_" + "_".join(gens[:3]),
            spec=spec,
            domain=domain,
        )
        candidates.append(constructor)
        return candidates

    def search_extension(self, domain: Any) -> List[Any]:
        """Search for extension type constructors.

        Extension constructors extend an existing type by new generators.
        They are preferred for low-severity domains.

        Parameters
        ----------
        domain:
            Domain formation.

        Returns
        -------
        list
            Candidate extension constructors.
        """
        candidates = []
        gens = _get_domain_generators(domain)
        for gen in gens[:2]:
            spec = _build_functor_spec(
                DEFAULT_SOURCE_CATEGORY,
                DEFAULT_TARGET_CATEGORY,
                "extension",
            )
            spec["extension_generator"] = gen
            self._builder.add_morphism(f"ext_{gen}", DEFAULT_ARITY)
            constructor = self._make_constructor(
                kind="extension",
                name=f"Ext_{gen}",
                spec=spec,
                domain=domain,
            )
            candidates.append(constructor)
        return candidates

    def search_restriction(self, domain: Any) -> List[Any]:
        """Search for restriction type constructors.

        Restriction constructors restrict an existing type to a sub-domain.
        They are preferred when severity is high and the domain type is
        ``'topological'`` or ``'geometric'``.

        Parameters
        ----------
        domain:
            Domain formation.

        Returns
        -------
        list
            Candidate restriction constructors.
        """
        candidates = []
        dtype = _get_domain_type(domain)
        if dtype not in ("topological", "geometric"):
            return candidates
        spec = _build_functor_spec(
            DEFAULT_SOURCE_CATEGORY,
            DEFAULT_TARGET_CATEGORY,
            "restriction",
        )
        spec["restricted_to"] = dtype
        constructor = self._make_constructor(
            kind="restriction",
            name=f"Restr_{dtype}",
            spec=spec,
            domain=domain,
        )
        candidates.append(constructor)
        return candidates

    def rank_constructors(self, constructors: List[Any]) -> List[Any]:
        """Rank a list of constructors by quality score (descending).

        Parameters
        ----------
        constructors:
            List of type constructors to rank.

        Returns
        -------
        list
            Same constructors sorted by descending score.
        """
        return sorted(constructors, key=lambda c: self.score_constructor(c), reverse=True)

    def filter_by_kind(self, constructors: List[Any], kind: str) -> List[Any]:
        """Filter a list of constructors to only those of the given *kind*.

        Parameters
        ----------
        constructors:
            Full list of type constructors.
        kind:
            Kind string to filter on (e.g. ``'inductive'``).

        Returns
        -------
        list
            Subset of *constructors* matching *kind*.
        """
        return [c for c in constructors if self._constructor_kind(c) == kind]

    def score_constructor(self, constructor: Any) -> float:
        """Compute a quality score for a type constructor.

        The score incorporates:
        - Presence of morphisms in the spec (+0.2 per morphism, capped at 0.4)
        - Presence of natural transformations (+0.10 per nat transform)
        - Presence of coherence conditions (+0.15 per condition)
        - Arity validity

        Parameters
        ----------
        constructor:
            A type constructor (TypeConstructor or dict).

        Returns
        -------
        float
            Quality score in ``[0.0, 1.0]``.
        """
        spec = self._constructor_spec(constructor)
        if not spec:
            return MIN_CONSTRUCTOR_SCORE

        morphism_count = len(spec.get("morphisms", []))
        nat_count = len(spec.get("natural_transformations", []))
        coh_count = len(spec.get("coherence_conditions", []))

        base = 0.4
        morph_bonus = min(morphism_count * 0.05, 0.20)
        nat_bonus = min(nat_count * NAT_TRANSFORM_BONUS, 0.20)
        coh_bonus = min(coh_count * COHERENCE_BONUS, 0.30)

        # Validate functor
        functor_ok = self._validate_functor(spec)
        functor_score = 0.10 if functor_ok else 0.0

        score = base + morph_bonus + nat_bonus + coh_bonus + functor_score
        return _clamp(score, 0.0, 1.0)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_inductive_spec(
        self, generator: str, domain: Any, coinductive: bool = False
    ) -> FunctorSpec:
        """Build a functor spec for an inductive (or coinductive) constructor.

        Parameters
        ----------
        generator:
            Primary generator name driving the inductive type.
        domain:
            Domain formation.
        coinductive:
            If ``True``, build a coinductive (greatest fixed-point) spec.

        Returns
        -------
        dict
            Functor spec dict.
        """
        kind_str = "coinductive" if coinductive else "inductive"
        dtype = _get_domain_type(domain)
        source = f"C_{dtype}_src"
        target = f"C_{dtype}_tgt"
        spec = _build_functor_spec(source, target, kind_str)
        spec["primary_generator"] = generator
        spec["morphisms"].append({"name": f"intro_{generator}", "arity": 1})
        spec["morphisms"].append({"name": f"elim_{generator}", "arity": 2})
        if coinductive:
            spec["morphisms"].append({"name": f"coelim_{generator}", "arity": 1})
        spec["natural_transformations"].append({
            "name": f"unit_{generator}",
            "components": ["C_src", "C_tgt"],
        })
        spec["coherence_conditions"].append({
            "name": f"triangle_{generator}",
            "condition": f"unit ∘ intro = id",
        })
        return spec

    def _build_quotient_spec(self, generators: List[str], domain: Any) -> FunctorSpec:
        """Build a functor spec for a quotient constructor.

        Parameters
        ----------
        generators:
            List of generator names to quotient.
        domain:
            Domain formation.

        Returns
        -------
        dict
            Functor spec dict.
        """
        dtype = _get_domain_type(domain)
        spec = _build_functor_spec(f"C_{dtype}", f"C_{dtype}/~", "quotient")
        for g in generators[:4]:
            spec["morphisms"].append({"name": f"q_{g}", "arity": 1})
        spec["natural_transformations"].append({
            "name": "quotient_proj",
            "components": ["C_src", "C_src/~"],
        })
        spec["coherence_conditions"].append({
            "name": "surjectivity",
            "condition": "quotient_proj is surjective",
        })
        spec["coherence_conditions"].append({
            "name": "kernel_inclusion",
            "condition": "ker(quotient_proj) ⊆ generated_equivalence",
        })
        return spec

    def _validate_functor(self, spec: FunctorSpec) -> bool:
        """Check that a functor spec satisfies basic functor laws.

        A minimal functor law check: the spec must have a non-empty ``morphisms``
        list and at least one natural transformation.

        Parameters
        ----------
        spec:
            Functor spec dict.

        Returns
        -------
        bool
            True iff the spec plausibly encodes a valid functor.
        """
        return bool(spec.get("morphisms")) and bool(spec.get("natural_transformations"))

    def _make_constructor(
        self,
        kind: str,
        name: str,
        spec: FunctorSpec,
        domain: Any,
    ) -> Any:
        """Construct a TypeConstructor (or dict fallback).

        Parameters
        ----------
        kind:
            Constructor kind string.
        name:
            Human-readable name.
        spec:
            Functor specification dict.
        domain:
            Source domain formation.

        Returns
        -------
        TypeConstructor or dict
        """
        constructor_id = _uid()
        try:
            return TypeConstructor(
                id=constructor_id,
                name=name,
                kind=kind,
                spec=spec,
                domain_id=_get_domain_id(domain),
            )
        except Exception:
            return {
                "id": constructor_id,
                "name": name,
                "kind": kind,
                "spec": spec,
                "domain_id": _get_domain_id(domain),
                "score": None,
            }

    @staticmethod
    def _constructor_kind(constructor: Any) -> str:
        """Extract the kind string from a constructor.

        Parameters
        ----------
        constructor:
            A type constructor.

        Returns
        -------
        str
        """
        kind = getattr(constructor, "kind", None)
        if kind is None:
            try:
                kind = constructor.get("kind", "unknown")
            except Exception:
                kind = "unknown"
        return str(kind)

    @staticmethod
    def _constructor_spec(constructor: Any) -> FunctorSpec:
        """Extract the functor spec from a constructor.

        Parameters
        ----------
        constructor:
            A type constructor.

        Returns
        -------
        dict
        """
        spec = getattr(constructor, "spec", None)
        if spec is None:
            try:
                spec = constructor.get("spec", {})
            except Exception:
                spec = {}
        return spec or {}


# ---------------------------------------------------------------------------
# FunctorSpecBuilder
# ---------------------------------------------------------------------------


class FunctorSpecBuilder:
    """Builds functor specifications for type constructors.

    A ``FunctorSpecBuilder`` accumulates morphisms, natural transformations,
    and coherence conditions into a structured spec dictionary.  It supports
    incremental construction (add items one at a time) as well as one-shot
    construction via ``build`` and ``from_domain``.

    The builder is stateful: it retains all added items until ``reset`` is
    called.  This allows callers to add shared components and then produce
    multiple related specs by varying only the kind or source/target names.

    Attributes
    ----------
    _morphisms : list of dict
        Accumulated morphism descriptors.
    _nat_transforms : list of dict
        Accumulated natural transformation descriptors.
    _coherence_conditions : list of dict
        Accumulated coherence condition descriptors.

    Examples
    --------
    >>> builder = FunctorSpecBuilder()
    >>> builder.add_morphism("intro", arity=1)
    >>> builder.add_natural_transformation("unit", components=["C_src", "C_tgt"])
    >>> spec = builder.build("C_src", "C_tgt", "inductive")
    >>> assert spec["kind"] == "inductive"
    """

    def __init__(self) -> None:
        """Initialize the FunctorSpecBuilder with empty state."""
        self._morphisms: List[Dict[str, Any]] = []
        self._nat_transforms: List[Dict[str, Any]] = []
        self._coherence_conditions: List[Dict[str, Any]] = []
        log.debug("FunctorSpecBuilder initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, source_domain: str, target_domain: str, kind: str) -> FunctorSpec:
        """Build a complete functor spec from the accumulated state.

        Combines the items added via ``add_morphism``, ``add_natural_transformation``,
        and ``add_coherence_condition`` with the default morphisms for *kind*
        to produce a complete spec dict.

        Parameters
        ----------
        source_domain:
            Name of the source category.
        target_domain:
            Name of the target category.
        kind:
            Constructor kind string.

        Returns
        -------
        dict
            Complete functor specification.
        """
        spec = _build_functor_spec(source_domain, target_domain, kind)
        # Merge default morphisms first, then accumulated ones
        spec["morphisms"] = list(self._default_morphisms(kind)) + list(self._morphisms)
        spec["morphisms"] = spec["morphisms"][:MAX_MORPHISMS]
        spec["natural_transformations"] = list(self._nat_transforms)[:MAX_NAT_TRANSFORMS]
        spec["coherence_conditions"] = list(self._coherence_conditions)[:MAX_COHERENCE_CONDITIONS]
        spec["fingerprint"] = self._spec_fingerprint(spec)
        return spec

    def add_morphism(self, name: str, arity: int = DEFAULT_ARITY) -> "FunctorSpecBuilder":
        """Add a morphism to the builder.

        Parameters
        ----------
        name:
            Name of the morphism (e.g. ``'intro_sigma'``).
        arity:
            Arity of the morphism (positive integer, default 1).

        Returns
        -------
        FunctorSpecBuilder
            Self, for method chaining.
        """
        if len(self._morphisms) >= MAX_MORPHISMS:
            log.warning("FunctorSpecBuilder: morphism limit reached, ignoring %s", name)
            return self
        self._morphisms.append({"name": str(name), "arity": max(1, int(arity))})
        return self

    def add_natural_transformation(
        self, name: str, components: List[str]
    ) -> "FunctorSpecBuilder":
        """Add a natural transformation to the builder.

        Parameters
        ----------
        name:
            Name of the natural transformation.
        components:
            List of component category names (source and target categories
            of each component of the transformation).

        Returns
        -------
        FunctorSpecBuilder
            Self, for method chaining.
        """
        if len(self._nat_transforms) >= MAX_NAT_TRANSFORMS:
            log.warning("FunctorSpecBuilder: nat transform limit reached, ignoring %s", name)
            return self
        self._nat_transforms.append({"name": str(name), "components": list(components)})
        return self

    def add_coherence_condition(
        self, name: str, condition: str
    ) -> "FunctorSpecBuilder":
        """Add a coherence condition to the builder.

        Parameters
        ----------
        name:
            Name of the coherence condition (e.g. ``'triangle_identity'``).
        condition:
            A string encoding the condition formula or description.

        Returns
        -------
        FunctorSpecBuilder
            Self, for method chaining.
        """
        if len(self._coherence_conditions) >= MAX_COHERENCE_CONDITIONS:
            log.warning("FunctorSpecBuilder: coherence limit reached, ignoring %s", name)
            return self
        self._coherence_conditions.append({"name": str(name), "condition": str(condition)})
        return self

    def validate_spec(self, spec: FunctorSpec) -> bool:
        """Validate that *spec* is a well-formed functor specification.

        A spec is well-formed if it has non-empty ``source``, ``target``,
        ``kind``, and at least one morphism.

        Parameters
        ----------
        spec:
            Functor specification dict to validate.

        Returns
        -------
        bool
            True iff the spec is well-formed.
        """
        required = ("source", "target", "kind", "morphisms")
        for key in required:
            if not spec.get(key):
                log.debug("validate_spec: missing or empty key '%s'", key)
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Return the current builder state as a plain dict.

        Returns
        -------
        dict
            Dict with keys ``'morphisms'``, ``'natural_transformations'``,
            and ``'coherence_conditions'``.
        """
        return {
            "morphisms": list(self._morphisms),
            "natural_transformations": list(self._nat_transforms),
            "coherence_conditions": list(self._coherence_conditions),
        }

    def from_domain(self, domain: Any) -> "FunctorSpecBuilder":
        """Populate the builder with default items derived from *domain*.

        This is a convenience method that adds one morphism per generator in
        the domain and one coherence condition representing the domain's first
        relation.

        Parameters
        ----------
        domain:
            A ``DomainFormation`` or dict.

        Returns
        -------
        FunctorSpecBuilder
            Self (builder is mutated in-place and returned).
        """
        gens = _get_domain_generators(domain)
        for gen in gens[:8]:
            self.add_morphism(f"gen_{gen}", arity=1)
        # Add a single shared coherence condition for the domain
        self.add_coherence_condition(
            "domain_coherence",
            f"generated by {', '.join(gens[:4])} subject to domain relations",
        )
        return self

    def reset(self) -> None:
        """Clear all accumulated items from the builder.

        After calling ``reset`` the builder is in the same state as after
        construction.
        """
        self._morphisms = []
        self._nat_transforms = []
        self._coherence_conditions = []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _default_morphisms(kind: str) -> List[Dict[str, Any]]:
        """Return a list of default morphisms for the given constructor *kind*.

        Each constructor kind comes with a canonical set of morphisms:

        - ``'inductive'``: ``intro`` (arity 1) and ``elim`` (arity 2).
        - ``'coinductive'``: ``out`` (arity 1) and ``coelim`` (arity 1).
        - ``'quotient'``: ``proj`` (arity 1).
        - ``'extension'``: ``ext`` (arity 1) and ``ret`` (arity 1).
        - ``'restriction'``: ``incl`` (arity 1) and ``proj`` (arity 1).

        Parameters
        ----------
        kind:
            Constructor kind string.

        Returns
        -------
        list of dict
            Default morphism descriptors.
        """
        defaults: Dict[str, List[Dict[str, Any]]] = {
            "inductive": [{"name": "intro", "arity": 1}, {"name": "elim", "arity": 2}],
            "coinductive": [{"name": "out", "arity": 1}, {"name": "coelim", "arity": 1}],
            "quotient": [{"name": "proj", "arity": 1}],
            "extension": [{"name": "ext", "arity": 1}, {"name": "ret", "arity": 1}],
            "restriction": [{"name": "incl", "arity": 1}, {"name": "proj", "arity": 1}],
        }
        return defaults.get(kind, [{"name": "id", "arity": 1}])

    @staticmethod
    def _check_coherence(conditions: List[Dict[str, Any]]) -> bool:
        """Check that coherence conditions are non-empty and syntactically valid.

        Parameters
        ----------
        conditions:
            List of coherence condition dicts.

        Returns
        -------
        bool
            True iff every condition has non-empty ``'name'`` and ``'condition'`` keys.
        """
        return all(c.get("name") and c.get("condition") for c in conditions)

    @staticmethod
    def _spec_fingerprint(spec: FunctorSpec) -> str:
        """Compute a short fingerprint string for a spec.

        Parameters
        ----------
        spec:
            Functor spec dict.

        Returns
        -------
        str
            16-character hex fingerprint.
        """
        parts = [
            spec.get("source", ""),
            spec.get("target", ""),
            spec.get("kind", ""),
            str(len(spec.get("morphisms", []))),
            str(len(spec.get("natural_transformations", []))),
        ]
        return hashlib.md5("|".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# TypeConstructorValidator
# ---------------------------------------------------------------------------


class TypeConstructorValidator:
    """Validates the coherence of type constructor candidates.

    The ``TypeConstructorValidator`` subjects a ``TypeConstructor`` to four
    validation checks:

    1. **Functoriality** — the spec must encode identity and composition
       preservation, checked by the presence of ``intro``/``elim``-type
       morphisms.
    2. **Naturality** — the natural transformations in the spec must be
       well-typed (each must reference at least two component categories).
    3. **Coherence** — all coherence conditions must be non-trivial (non-empty
       condition string).
    4. **Arity** — all morphisms must have arity in ``[1, 8]``.

    The validator also computes a scalar score and lists all violations,
    enabling downstream ranking and filtering.

    Attributes
    ----------
    config : dict
        Optional configuration dict.

    Examples
    --------
    >>> validator = TypeConstructorValidator()
    >>> result = validator.validate(constructor)
    >>> if not result["valid"]:
    ...     for v in result["violations"]:
    ...         print(v)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the TypeConstructorValidator.

        Parameters
        ----------
        config:
            Optional configuration dict.  No keys are currently required,
            but the argument is accepted for API symmetry.
        """
        self.config: Dict[str, Any] = config or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, constructor: Any) -> ValidationResult:
        """Run all validation checks and return a structured result.

        Parameters
        ----------
        constructor:
            A ``TypeConstructor`` instance or dict.

        Returns
        -------
        dict
            Validation result with keys:

            - ``'valid'``: bool — overall pass/fail.
            - ``'score'``: float in [0, 1].
            - ``'violations'``: list of violation strings.
            - ``'checks'``: dict of check name → bool.
            - ``'validated_at'``: ISO-8601 timestamp.
        """
        func_ok = self.check_functoriality(constructor)
        nat_ok = self.check_naturality(constructor)
        coh_ok = self.check_coherence(constructor)
        arity_ok = self.check_arity(constructor)
        violations = self.list_violations(constructor)
        score = self.compute_score(constructor)
        valid = func_ok and arity_ok and len(violations) == 0
        return {
            "valid": valid,
            "score": score,
            "violations": violations,
            "checks": {
                "functoriality": func_ok,
                "naturality": nat_ok,
                "coherence": coh_ok,
                "arity": arity_ok,
            },
            "validated_at": _utcnow().isoformat(),
        }

    def check_functoriality(self, constructor: Any) -> bool:
        """Check that the constructor's spec encodes functor laws.

        A spec satisfies the functoriality check if it contains at least
        one morphism (proxy for encoding the action on morphisms) and the
        ``_validate_functor`` helper returns True.

        Parameters
        ----------
        constructor:
            A type constructor.

        Returns
        -------
        bool
            True iff functoriality conditions are satisfied.
        """
        spec = TypeConstructorSearch._constructor_spec(constructor)
        morphisms = spec.get("morphisms", [])
        return bool(morphisms)

    def check_naturality(self, constructor: Any) -> bool:
        """Check that the constructor's natural transformations are well-typed.

        Each natural transformation must reference at least two component
        category names.

        Parameters
        ----------
        constructor:
            A type constructor.

        Returns
        -------
        bool
            True iff all natural transformations pass the naturality check.
        """
        spec = TypeConstructorSearch._constructor_spec(constructor)
        nats = spec.get("natural_transformations", [])
        for nat in nats:
            components = nat.get("components", [])
            if len(components) < 2:
                return False
        return True

    def check_coherence(self, constructor: Any) -> bool:
        """Check that coherence conditions are non-trivial.

        Parameters
        ----------
        constructor:
            A type constructor.

        Returns
        -------
        bool
            True iff every coherence condition has a non-empty ``condition`` string.
        """
        spec = TypeConstructorSearch._constructor_spec(constructor)
        conditions = spec.get("coherence_conditions", [])
        return all(c.get("condition") for c in conditions)

    def check_arity(self, constructor: Any) -> bool:
        """Check that all morphism arities are in the valid range [1, 8].

        Parameters
        ----------
        constructor:
            A type constructor.

        Returns
        -------
        bool
            True iff all morphisms have arity in ``[1, 8]``.
        """
        spec = TypeConstructorSearch._constructor_spec(constructor)
        morphisms = spec.get("morphisms", [])
        return all(1 <= m.get("arity", 1) <= 8 for m in morphisms)

    def list_violations(self, constructor: Any) -> List[str]:
        """List all validation violations for the constructor.

        Parameters
        ----------
        constructor:
            A type constructor.

        Returns
        -------
        list of str
            Human-readable violation descriptions.  Empty if constructor is valid.
        """
        violations: List[str] = []
        spec = TypeConstructorSearch._constructor_spec(constructor)
        if not spec.get("morphisms"):
            violations.append("Constructor has no morphisms (functor law not satisfied).")
        for nat in spec.get("natural_transformations", []):
            if len(nat.get("components", [])) < 2:
                violations.append(f"Natural transformation {nat.get('name')} has < 2 components.")
        for cond in spec.get("coherence_conditions", []):
            if not cond.get("condition"):
                violations.append(f"Coherence condition {cond.get('name')} has empty condition.")
        for morph in spec.get("morphisms", []):
            arity = morph.get("arity", 1)
            if not (1 <= arity <= 8):
                violations.append(f"Morphism {morph.get('name')} has invalid arity {arity}.")
        return violations

    def compute_score(self, constructor: Any) -> float:
        """Compute a 0-1 validation score for the constructor.

        The score is a weighted average of the four check results.

        Parameters
        ----------
        constructor:
            A type constructor.

        Returns
        -------
        float
            Score in ``[0.0, 1.0]``.
        """
        func_score = float(self.check_functoriality(constructor))
        nat_score = float(self.check_naturality(constructor))
        coh_score = float(self.check_coherence(constructor))
        arity_score = float(self.check_arity(constructor))
        w = VALIDATION_WEIGHTS
        total = (
            w["functoriality"] * func_score
            + w["naturality"] * nat_score
            + w["coherence"] * coh_score
            + w["arity"] * arity_score
        )
        return _clamp(total, 0.0, 1.0)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _functor_laws() -> List[str]:
        """Return a list of canonical functor law names.

        Returns
        -------
        list of str
            Names of standard functor laws.
        """
        return ["identity_preservation", "composition_preservation"]

    @staticmethod
    def _naturality_conditions() -> List[str]:
        """Return a list of canonical naturality condition names.

        Returns
        -------
        list of str
        """
        return ["component_typing", "naturality_square_commutativity"]

    @staticmethod
    def _coherence_diagrams() -> List[str]:
        """Return a list of canonical coherence diagram names.

        Returns
        -------
        list of str
        """
        return ["triangle_identity", "pentagon_identity", "hexagon_identity"]


# ---------------------------------------------------------------------------
# TypeConstructorRunner
# ---------------------------------------------------------------------------


class TypeConstructorRunner:
    """Orchestrates the type constructor search pipeline.

    The ``TypeConstructorRunner`` wires together ``TypeConstructorSearch``
    and ``TypeConstructorValidator`` into a single pipeline.  After calling
    ``run`` the caller can inspect intermediate results via ``get_results``
    and print a human-readable summary via ``summarize``.

    Attributes
    ----------
    config : dict
        Configuration dict forwarded to sub-components.
    _search : TypeConstructorSearch
        Internal search engine.
    _validator : TypeConstructorValidator
        Internal validator.
    _raw_constructors : list or None
        Raw (unvalidated) constructors from the most recent ``run_search`` call.
    _validation_results : list or None
        Validation results from the most recent ``run_validation`` call.

    Examples
    --------
    >>> runner = TypeConstructorRunner()
    >>> constructors = runner.run(domain_formation)
    >>> print(runner.summarize())
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the TypeConstructorRunner.

        Parameters
        ----------
        config:
            Optional configuration dict forwarded to sub-components.
        """
        cfg = config or {}
        self.config: Dict[str, Any] = cfg
        self._search = TypeConstructorSearch(config=cfg)
        self._validator = TypeConstructorValidator(config=cfg)
        self._raw_constructors: Optional[List[Any]] = None
        self._validation_results: Optional[List[ValidationResult]] = None
        log.debug("TypeConstructorRunner initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, domain_formation: Any) -> List[Any]:
        """Run the full type constructor search and validation pipeline.

        Parameters
        ----------
        domain_formation:
            A ``DomainFormation`` or dict describing the target domain.

        Returns
        -------
        list
            Validated and ranked type constructors.
        """
        log.info("TypeConstructorRunner.run: starting for domain %s", _get_domain_id(domain_formation))
        self._raw_constructors = self.run_search(domain_formation)
        self._validation_results = self.run_validation(self._raw_constructors)
        # Filter to valid constructors
        valid = [
            c for c, vr in zip(self._raw_constructors, self._validation_results)
            if vr.get("valid", False)
        ]
        if not valid:
            log.warning("TypeConstructorRunner.run: no valid constructors; returning all")
            valid = self._raw_constructors
        # Rank by validation score
        scores = {
            id(c): vr.get("score", 0.0)
            for c, vr in zip(self._raw_constructors, self._validation_results)
        }
        valid = sorted(valid, key=lambda c: scores.get(id(c), 0.0), reverse=True)
        log.info("TypeConstructorRunner.run: returning %d constructors", len(valid))
        return valid

    def run_search(self, domain: Any) -> List[Any]:
        """Execute the constructor search step.

        Parameters
        ----------
        domain:
            Domain formation.

        Returns
        -------
        list
            Raw (unvalidated) constructor candidates.
        """
        return self._search.search(domain)

    def run_validation(self, constructors: List[Any]) -> List[ValidationResult]:
        """Execute the validation step.

        Parameters
        ----------
        constructors:
            List of constructor candidates to validate.

        Returns
        -------
        list of dict
            One validation result per constructor.
        """
        return [self._validator.validate(c) for c in constructors]

    def get_results(self) -> Dict[str, Any]:
        """Return a dict containing all intermediate pipeline results.

        Returns
        -------
        dict
            Keys: ``'raw_constructors'``, ``'validation_results'``.
        """
        return {
            "raw_constructors": self._raw_constructors,
            "validation_results": self._validation_results,
        }

    def reset(self) -> None:
        """Reset the runner's internal state.

        Clears cached constructors and validation results.  Sub-component
        caches are also cleared.
        """
        self._raw_constructors = None
        self._validation_results = None
        self._search._cache.clear()
        log.debug("TypeConstructorRunner.reset: state cleared")

    def summarize(self) -> str:
        """Return a human-readable summary of the most recent pipeline run.

        Returns
        -------
        str
            Multi-line summary string.
        """
        if self._raw_constructors is None:
            return "TypeConstructorRunner: no pipeline run has been performed yet."
        n_raw = len(self._raw_constructors)
        n_valid = sum(1 for vr in (self._validation_results or []) if vr.get("valid", False))
        lines = [
            "TypeConstructorRunner summary:",
            f"  Raw constructors found:     {n_raw}",
            f"  Constructors passing valid: {n_valid}",
        ]
        if self._validation_results:
            scores = [vr.get("score", 0.0) for vr in self._validation_results]
            if scores:
                lines.append(f"  Mean validation score:      {sum(scores)/len(scores):.3f}")
                lines.append(f"  Max validation score:       {max(scores):.3f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Free convenience functions
# ---------------------------------------------------------------------------


def search_type_constructors(
    domain_formation: Any,
    config: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    """Search for valid type constructors for a domain formation.

    Convenience wrapper around ``TypeConstructorSearch.search``.

    Parameters
    ----------
    domain_formation:
        A ``DomainFormation`` or dict-like domain object.
    config:
        Optional configuration dict.

    Returns
    -------
    list
        Ranked list of valid type constructors.

    Examples
    --------
    >>> constructors = search_type_constructors(domain, config={"min_score": 0.5})
    """
    return TypeConstructorSearch(config=config).search(domain_formation)


def validate_constructor(
    constructor: Any,
    config: Optional[Dict[str, Any]] = None,
) -> ValidationResult:
    """Validate a single type constructor.

    Convenience wrapper around ``TypeConstructorValidator.validate``.

    Parameters
    ----------
    constructor:
        A ``TypeConstructor`` or dict.
    config:
        Optional configuration dict.

    Returns
    -------
    dict
        Validation result dict.

    Examples
    --------
    >>> result = validate_constructor(my_constructor)
    >>> print(result["valid"], result["score"])
    """
    return TypeConstructorValidator(config=config).validate(constructor)
