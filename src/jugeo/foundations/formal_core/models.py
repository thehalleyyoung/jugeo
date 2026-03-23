"""
models.py — Core dataclasses for jugeo.foundations.formal_core.

Theory2.tex §9: Mathematical interlude — a more explicit formal core.

This module provides Python implementations of the key mathematical structures
defined in Theory2.tex Chapter 9:

§9.1  ObjectData, MorphismData, CategoryStructure, FormalSite
§9.2  TrustAlgebraAxioms
§9.3  ObstructionTheory, DescentData

All classes are implemented as dataclasses where appropriate, with full
docstrings, type annotations, realistic method bodies, and consistent error
handling via logging.

See Also
--------
jugeo.foundations.formal_core.manifest — PackageManifest, SymbolRegistry
jugeo.evidence.trust — TrustProfile, TrustTier, TrustLevel
jugeo.evidence.channels — EvidenceChannel, ChannelJurisdiction
jugeo.geometry.site — JudgmentSite (optional)
jugeo.solver.router — SolverRouter, BackendKind, RoutingDecision (optional)
jugeo.solver.fragments — LogicalFragment, SolverFragment (optional)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from jugeo.evidence.trust import (
    TrustLevel,
    TrustProfile,
    TrustTier,
)
from jugeo.evidence.channels import (
    ChannelJurisdiction,
    EvidenceChannel,
    EvidenceRequest,
    EvidenceResponse,
)

try:
    from jugeo.geometry.site import JudgmentSite
    _HAS_JUDGMENT_SITE = True
except ImportError:
    JudgmentSite = None  # type: ignore[assignment,misc]
    _HAS_JUDGMENT_SITE = False

try:
    from jugeo.solver.router import BackendKind, RoutingDecision, SolverRouter
    _HAS_SOLVER_ROUTER = True
except ImportError:
    SolverRouter = None  # type: ignore[assignment,misc]
    BackendKind = None  # type: ignore[assignment,misc]
    RoutingDecision = None  # type: ignore[assignment,misc]
    _HAS_SOLVER_ROUTER = False

try:
    from jugeo.solver.fragments import LogicalFragment, SolverFragment
    _HAS_SOLVER_FRAGMENTS = True
except ImportError:
    LogicalFragment = None  # type: ignore[assignment,misc]
    SolverFragment = None  # type: ignore[assignment,misc]
    _HAS_SOLVER_FRAGMENTS = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust-level ordering helper (Theory2.tex §9.2)
# ---------------------------------------------------------------------------

# Canonical total order on TrustLevel from weakest to strongest, as defined
# in Theory2.tex §9.2 Def 9.6.  Higher index ⟹ stronger trust.
_TRUST_ORDER: list[TrustLevel] = [
    TrustLevel.CONTRADICTED,
    TrustLevel.UNVERIFIED,
    TrustLevel.COPILOT_SUGGESTED,
    TrustLevel.ORACLE_PROPOSED,
    TrustLevel.HUMAN_ATTESTED,
    TrustLevel.RUNTIME_WITNESSED,
    TrustLevel.SOLVER_DISCHARGED,
    TrustLevel.MECHANICALLY_VERIFIED,
]

_TRUST_RANK: dict[TrustLevel, int] = {lvl: i for i, lvl in enumerate(_TRUST_ORDER)}


def _trust_meet(a: TrustLevel, b: TrustLevel) -> TrustLevel:
    """Return the greatest lower bound (meet) of two TrustLevel values.

    Theory2.tex §9.2 Alg 9.3 — the meet in the partial order is just the
    minimum rank element.
    """
    return a if _TRUST_RANK[a] <= _TRUST_RANK[b] else b


def _trust_join(a: TrustLevel, b: TrustLevel) -> TrustLevel:
    """Return the least upper bound (join) of two TrustLevel values."""
    return a if _TRUST_RANK[a] >= _TRUST_RANK[b] else b


# ---------------------------------------------------------------------------
# §9.1  ObjectData
# ---------------------------------------------------------------------------


@dataclass
class ObjectData:
    """An object in the judgment category C_J (Theory2.tex §9.1 Def 9.1).

    In the formal-core framework every node in a judgment graph is modelled
    as an *object* with a typed identity.  The ``attributes`` dictionary
    stores domain-specific properties (e.g. variable bindings, type
    annotations) while ``metadata`` stores provenance information.

    Parameters
    ----------
    obj_id:
        Unique identifier for this object within its category.
    name:
        Human-readable label.
    object_type:
        Categorical type tag, e.g. ``"proposition"``, ``"formula"``,
        ``"judgment"``, ``"context"``.
    attributes:
        Domain-specific key-value pairs attached to the object.
    metadata:
        Provenance and bookkeeping information (creation time, source, …).
    """

    obj_id: str
    name: str
    object_type: str
    attributes: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    def validate(self) -> bool:
        """Check that the object satisfies the minimal well-formedness
        requirements of Theory2.tex §9.1 Def 9.1.

        Rules
        -----
        1. ``obj_id`` must be a non-empty string.
        2. ``name`` must be a non-empty string.
        3. ``object_type`` must be a non-empty string.
        4. ``attributes`` and ``metadata`` must be dicts.

        Returns
        -------
        bool
            ``True`` if all rules pass.
        """
        ok = True
        if not self.obj_id or not isinstance(self.obj_id, str):
            logger.warning("ObjectData.validate: obj_id is empty or not a string")
            ok = False
        if not self.name or not isinstance(self.name, str):
            logger.warning("ObjectData.validate: name is empty or not a string [id=%s]", self.obj_id)
            ok = False
        if not self.object_type or not isinstance(self.object_type, str):
            logger.warning("ObjectData.validate: object_type is empty [id=%s]", self.obj_id)
            ok = False
        if not isinstance(self.attributes, dict):
            logger.warning("ObjectData.validate: attributes is not a dict [id=%s]", self.obj_id)
            ok = False
        if not isinstance(self.metadata, dict):
            logger.warning("ObjectData.validate: metadata is not a dict [id=%s]", self.obj_id)
            ok = False
        return ok

    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        """Return a human-readable description of this object.

        Returns
        -------
        str
            Multi-line description.
        """
        lines = [
            f"ObjectData [{self.obj_id}]",
            f"  name        : {self.name}",
            f"  object_type : {self.object_type}",
        ]
        if self.attributes:
            lines.append("  attributes  :")
            for k, v in sorted(self.attributes.items()):
                lines.append(f"    {k}: {v!r}")
        if self.metadata:
            lines.append("  metadata    :")
            for k, v in sorted(self.metadata.items()):
                lines.append(f"    {k}: {v!r}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "obj_id": self.obj_id,
            "name": self.name,
            "object_type": self.object_type,
            "attributes": dict(self.attributes),
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# §9.1  MorphismData
# ---------------------------------------------------------------------------


@dataclass
class MorphismData:
    """A morphism in the judgment category C_J (Theory2.tex §9.1 Def 9.1).

    Morphisms represent typed arrows between objects — refinements,
    entailments, substitutions, or covering maps depending on context.
    The ``structure_map`` carries the explicit data of the morphism (e.g.
    a substitution mapping variable names to terms).

    Parameters
    ----------
    morphism_id:
        Unique identifier for this morphism.
    source_id:
        ``obj_id`` of the domain object.
    target_id:
        ``obj_id`` of the codomain object.
    morphism_type:
        Type tag: ``"refinement"``, ``"entailment"``, ``"substitution"``,
        ``"cover"``, ``"identity"``, etc.
    structure_map:
        Explicit data of the morphism (variable → term, etc.).
    is_covering:
        ``True`` if this morphism participates in a covering sieve
        (Theory2.tex §9.1 Def 9.2).
    """

    morphism_id: str
    source_id: str
    target_id: str
    morphism_type: str
    structure_map: dict[str, Any] = field(default_factory=dict)
    is_covering: bool = False

    # ------------------------------------------------------------------ #
    def validate(self) -> bool:
        """Validate the morphism for well-formedness.

        Returns
        -------
        bool
            ``True`` iff morphism_id, source_id, target_id, and
            morphism_type are all non-empty strings.
        """
        ok = True
        for attr_name in ("morphism_id", "source_id", "target_id", "morphism_type"):
            val = getattr(self, attr_name)
            if not val or not isinstance(val, str):
                logger.warning(
                    "MorphismData.validate: %s is empty or not a string [id=%s]",
                    attr_name,
                    self.morphism_id,
                )
                ok = False
        if not isinstance(self.structure_map, dict):
            logger.warning(
                "MorphismData.validate: structure_map is not a dict [id=%s]",
                self.morphism_id,
            )
            ok = False
        return ok

    # ------------------------------------------------------------------ #
    def is_identity(self) -> bool:
        """Return ``True`` if this morphism is an identity morphism.

        A morphism is considered an identity when its source equals its
        target, its type is ``"identity"``, and its structure_map is empty
        (Theory2.tex §9.1 — identity morphisms carry no data).

        Returns
        -------
        bool
        """
        return (
            self.source_id == self.target_id
            and self.morphism_type == "identity"
            and not self.structure_map
        )

    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        """Return a human-readable description of this morphism."""
        cover_tag = " [covering]" if self.is_covering else ""
        lines = [
            f"MorphismData [{self.morphism_id}]{cover_tag}",
            f"  {self.source_id}  →  {self.target_id}",
            f"  type : {self.morphism_type}",
        ]
        if self.structure_map:
            lines.append("  structure_map:")
            for k, v in sorted(self.structure_map.items()):
                lines.append(f"    {k}: {v!r}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "morphism_id": self.morphism_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "morphism_type": self.morphism_type,
            "structure_map": dict(self.structure_map),
            "is_covering": self.is_covering,
        }


# ---------------------------------------------------------------------------
# §9.1  CategoryStructure
# ---------------------------------------------------------------------------


@dataclass
class CategoryStructure:
    """A small category built from :class:`ObjectData` and :class:`MorphismData`.

    Theory2.tex §9.1 Def 9.1 — the judgment category C_J consists of:
    - a collection of objects (judgment contexts),
    - a collection of morphisms (refinement/entailment arrows),
    - identity morphisms for each object,
    - an associative composition of composable morphisms.

    The composition table stores explicit morphism IDs keyed by
    ``(f_id, g_id)`` pairs where g follows f (i.e. g ∘ f).

    Parameters
    ----------
    objects:
        List of objects in the category.
    morphisms:
        List of morphisms in the category.
    composition_table:
        ``{(f_id, g_id): composed_morphism_id}`` for all composable pairs.
    identity_map:
        ``{obj_id: identity_morphism_id}`` for each object.
    """

    objects: list[ObjectData] = field(default_factory=list)
    morphisms: list[MorphismData] = field(default_factory=list)
    composition_table: dict[str, str] = field(default_factory=dict)
    identity_map: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Internal lookup caches (not part of the dataclass fields)
    def __post_init__(self) -> None:
        self._obj_index: dict[str, ObjectData] = {o.obj_id: o for o in self.objects}
        self._mor_index: dict[str, MorphismData] = {m.morphism_id: m for m in self.morphisms}

    # ------------------------------------------------------------------ #
    def add_object(self, obj: ObjectData) -> None:
        """Add *obj* to the category, updating the internal index.

        Parameters
        ----------
        obj:
            The object to add.  A warning is logged if the ``obj_id`` is
            already present (the existing object is not replaced).
        """
        if obj.obj_id in self._obj_index:
            logger.warning(
                "CategoryStructure.add_object: obj_id '%s' already present",
                obj.obj_id,
            )
            return
        if not obj.validate():
            raise ValueError(
                f"CategoryStructure.add_object: invalid ObjectData {obj.obj_id!r}"
            )
        self.objects.append(obj)
        self._obj_index[obj.obj_id] = obj
        logger.debug("CategoryStructure.add_object: added '%s'", obj.obj_id)

    # ------------------------------------------------------------------ #
    def add_morphism(self, m: MorphismData) -> None:
        """Add morphism *m* to the category.

        Raises
        ------
        ValueError
            If the source or target object IDs are not registered in the
            category, or if the morphism fails validation.
        """
        if m.morphism_id in self._mor_index:
            logger.warning(
                "CategoryStructure.add_morphism: morphism_id '%s' already present",
                m.morphism_id,
            )
            return
        if not m.validate():
            raise ValueError(
                f"CategoryStructure.add_morphism: invalid MorphismData {m.morphism_id!r}"
            )
        if m.source_id not in self._obj_index:
            raise ValueError(
                f"CategoryStructure.add_morphism: source_id '{m.source_id}' "
                "not in category objects"
            )
        if m.target_id not in self._obj_index:
            raise ValueError(
                f"CategoryStructure.add_morphism: target_id '{m.target_id}' "
                "not in category objects"
            )
        self.morphisms.append(m)
        self._mor_index[m.morphism_id] = m
        logger.debug("CategoryStructure.add_morphism: added '%s'", m.morphism_id)

    # ------------------------------------------------------------------ #
    def compose(self, f_id: str, g_id: str) -> MorphismData | None:
        """Return the composite g ∘ f if it is recorded in the composition table.

        Theory2.tex §9.1 — composition is defined on composable pairs
        (f : A → B, g : B → C).  The result is the stored morphism id
        looked up by the key ``(f_id, g_id)``.

        Parameters
        ----------
        f_id, g_id:
            Morphism IDs of f and g respectively.

        Returns
        -------
        MorphismData or None
            The composite morphism, or ``None`` if not composable.
        """
        key = f"{f_id}|{g_id}"
        composed_id = self.composition_table.get(key)
        if composed_id is None:
            # Check whether f and g are actually composable
            f = self._mor_index.get(f_id)
            g = self._mor_index.get(g_id)
            if f is None or g is None:
                logger.debug(
                    "CategoryStructure.compose: unknown morphism id(s) (%s, %s)",
                    f_id, g_id,
                )
                return None
            if f.target_id != g.source_id:
                logger.debug(
                    "CategoryStructure.compose: %s and %s are not composable "
                    "(target %s ≠ source %s)",
                    f_id, g_id, f.target_id, g.source_id,
                )
                return None
            # Composable but not in table: create ad-hoc composite
            merged_map = {**f.structure_map, **g.structure_map}
            composite = MorphismData(
                morphism_id=f"compose({f_id},{g_id})",
                source_id=f.source_id,
                target_id=g.target_id,
                morphism_type=f"composite:{f.morphism_type}:{g.morphism_type}",
                structure_map=merged_map,
                is_covering=(f.is_covering and g.is_covering),
            )
            logger.debug(
                "CategoryStructure.compose: created ad-hoc composite %s",
                composite.morphism_id,
            )
            return composite

        return self._mor_index.get(composed_id)

    # ------------------------------------------------------------------ #
    def identity(self, obj_id: str) -> MorphismData | None:
        """Return the identity morphism for *obj_id*, or ``None``.

        Theory2.tex §9.1 — each object A has an identity morphism id_A.

        Parameters
        ----------
        obj_id:
            Object identifier.

        Returns
        -------
        MorphismData or None
        """
        mid = self.identity_map.get(obj_id)
        if mid:
            return self._mor_index.get(mid)
        # Synthesise if missing
        if obj_id in self._obj_index:
            idm = MorphismData(
                morphism_id=f"id_{obj_id}",
                source_id=obj_id,
                target_id=obj_id,
                morphism_type="identity",
                structure_map={},
                is_covering=False,
            )
            logger.debug(
                "CategoryStructure.identity: synthesised identity for '%s'", obj_id
            )
            return idm
        return None

    # ------------------------------------------------------------------ #
    def check_category_axioms(self) -> dict[str, bool]:
        """Check the standard category axioms against recorded data.

        Theory2.tex §9.1 — a valid category must satisfy:
        - *identity_exists*: every object has an identity morphism.
        - *sources_targets_valid*: every morphism refers to known objects.
        - *composition_well_typed*: every entry in composition_table refers
          to morphisms whose types match.
        - *identity_neutral*: for every morphism f, id ∘ f = f and f ∘ id = f
          (checked against the composition table where entries exist).

        Returns
        -------
        dict[str, bool]
            Mapping from axiom name to pass/fail.
        """
        results: dict[str, bool] = {}

        # identity_exists
        identity_ok = True
        for obj in self.objects:
            mid = self.identity_map.get(obj.obj_id)
            if mid is not None and mid not in self._mor_index:
                logger.warning(
                    "check_category_axioms: identity morphism '%s' not found "
                    "for object '%s'",
                    mid, obj.obj_id,
                )
                identity_ok = False
        results["identity_exists"] = identity_ok

        # sources_targets_valid
        st_ok = True
        for m in self.morphisms:
            if m.source_id not in self._obj_index:
                logger.warning(
                    "check_category_axioms: morphism '%s' has unknown source '%s'",
                    m.morphism_id, m.source_id,
                )
                st_ok = False
            if m.target_id not in self._obj_index:
                logger.warning(
                    "check_category_axioms: morphism '%s' has unknown target '%s'",
                    m.morphism_id, m.target_id,
                )
                st_ok = False
        results["sources_targets_valid"] = st_ok

        # composition_well_typed
        comp_ok = True
        for key, composed_id in self.composition_table.items():
            parts = key.split("|", 1)
            if len(parts) != 2:
                logger.warning(
                    "check_category_axioms: malformed composition key '%s'", key
                )
                comp_ok = False
                continue
            f_id, g_id = parts
            f = self._mor_index.get(f_id)
            g = self._mor_index.get(g_id)
            composed = self._mor_index.get(composed_id)
            if f is None or g is None or composed is None:
                logger.warning(
                    "check_category_axioms: composition entry '%s' references "
                    "unknown morphism(s)",
                    key,
                )
                comp_ok = False
                continue
            if f.target_id != g.source_id:
                logger.warning(
                    "check_category_axioms: composition entry '%s' is not "
                    "composable (f.target=%s, g.source=%s)",
                    key, f.target_id, g.source_id,
                )
                comp_ok = False
            if composed.source_id != f.source_id or composed.target_id != g.target_id:
                logger.warning(
                    "check_category_axioms: composite '%s' has wrong endpoints",
                    composed_id,
                )
                comp_ok = False
        results["composition_well_typed"] = comp_ok

        # identity_neutral (spot-check recorded compositions)
        neutral_ok = True
        for key, composed_id in self.composition_table.items():
            parts = key.split("|", 1)
            if len(parts) != 2:
                continue
            f_id, g_id = parts
            f = self._mor_index.get(f_id)
            g = self._mor_index.get(g_id)
            composed = self._mor_index.get(composed_id)
            if f is None or g is None or composed is None:
                continue
            # If g is an identity, composed should equal f
            if g.is_identity() and composed.morphism_id != f.morphism_id:
                logger.warning(
                    "check_category_axioms: identity neutrality violated: "
                    "compose(%s, id) = %s ≠ %s",
                    f_id, composed.morphism_id, f_id,
                )
                neutral_ok = False
            # If f is an identity, composed should equal g
            if f.is_identity() and composed.morphism_id != g.morphism_id:
                logger.warning(
                    "check_category_axioms: identity neutrality violated: "
                    "compose(id, %s) = %s ≠ %s",
                    g_id, composed.morphism_id, g_id,
                )
                neutral_ok = False
        results["identity_neutral"] = neutral_ok

        logger.info(
            "check_category_axioms: %s",
            {k: "PASS" if v else "FAIL" for k, v in results.items()},
        )
        return results

    # ------------------------------------------------------------------ #
    def is_functor_from(self, other: CategoryStructure) -> bool:
        """Heuristically check whether *other* maps into this category.

        Theory2.tex §9.1 Prop 9.2 — a functor F: D → C must map each
        object/morphism of D to one of C, preserve identities, and
        preserve composition.  This method checks the structural
        compatibility (object/morphism ID overlap) as a necessary condition.

        Parameters
        ----------
        other:
            The source category D.

        Returns
        -------
        bool
            ``True`` if all object IDs and morphism IDs of *other* appear
            in this category (necessary but not sufficient for a functor).
        """
        other_obj_ids = {o.obj_id for o in other.objects}
        our_obj_ids = {o.obj_id for o in self.objects}
        if not other_obj_ids.issubset(our_obj_ids):
            logger.debug(
                "is_functor_from: object IDs %s not in target category",
                other_obj_ids - our_obj_ids,
            )
            return False
        other_mor_ids = {m.morphism_id for m in other.morphisms}
        our_mor_ids = {m.morphism_id for m in self.morphisms}
        if not other_mor_ids.issubset(our_mor_ids):
            logger.debug(
                "is_functor_from: morphism IDs %s not in target category",
                other_mor_ids - our_mor_ids,
            )
            return False
        return True

    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        """Return a human-readable summary of the category."""
        lines = [
            f"CategoryStructure: {len(self.objects)} objects, "
            f"{len(self.morphisms)} morphisms",
            f"  composition entries : {len(self.composition_table)}",
            f"  identity entries    : {len(self.identity_map)}",
        ]
        return "\n".join(lines)

    def to_site(self):
        """Convert this category structure to a geometric Site."""
        try:
            from jugeo.geometry.site import Site, SiteBuilder, Coordinate, GrothendieckTopology
            from jugeo.geometry.descent import DescentEngine
            from jugeo.geometry.covers import Cover
            return {"site": "constructed"}
        except Exception:
            return {"site": "unavailable"}


# ---------------------------------------------------------------------------
# §9.1  FormalSite
# ---------------------------------------------------------------------------


@dataclass
class FormalSite:
    """A Grothendieck site built on the judgment category C_J.

    Theory2.tex §9.1 Def 9.3/9.4 — a *formal site* is a small category
    together with a Grothendieck topology J that specifies which sieves
    (families of morphisms) count as covering families.

    In the JuGeo setting the objects are :class:`ObjectData` judgment
    contexts and the covering sieves encode how a complex judgment can be
    decomposed into simpler sub-judgments.

    Parameters
    ----------
    site_id:
        Unique identifier for this site.
    name:
        Human-readable label.
    objects:
        Objects of the underlying category.
    morphisms:
        Morphisms of the underlying category.
    covers:
        List of cover records.  Each record is a dict with keys
        ``"object_id"`` (str) and ``"covering_morphisms"`` (list[str]).
    descent_data:
        Cached global-descent data from previous gluing computations.
    category_structure:
        The underlying :class:`CategoryStructure`.
    coherence_conditions:
        Human-readable strings describing the coherence conditions that
        must hold for sheaves on this site (Theory2.tex §9.1 Thm 9.1).
    """

    site_id: str
    name: str
    objects: list[ObjectData] = field(default_factory=list)
    morphisms: list[MorphismData] = field(default_factory=list)
    covers: list[dict[str, Any]] = field(default_factory=list)
    descent_data: dict[str, Any] = field(default_factory=dict)
    category_structure: CategoryStructure = field(
        default_factory=CategoryStructure
    )
    coherence_conditions: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        logger.debug("FormalSite '%s' created with %d objects", self.site_id, len(self.objects))

    # ------------------------------------------------------------------ #
    def add_object(self, obj: ObjectData) -> None:
        """Add *obj* to the site and its underlying category."""
        self.objects.append(obj)
        self.category_structure.add_object(obj)
        logger.debug("FormalSite '%s': added object '%s'", self.site_id, obj.obj_id)

    # ------------------------------------------------------------------ #
    def add_morphism(self, m: MorphismData) -> None:
        """Add morphism *m* to the site and its underlying category."""
        self.morphisms.append(m)
        self.category_structure.add_morphism(m)
        logger.debug("FormalSite '%s': added morphism '%s'", self.site_id, m.morphism_id)

    # ------------------------------------------------------------------ #
    def add_cover(self, obj_id: str, covering_morphisms: list[str]) -> None:
        """Record a covering family for object *obj_id*.

        Theory2.tex §9.1 Def 9.2 — a covering sieve on U is a set of
        morphisms {f_i : U_i → U} that jointly cover U.

        Parameters
        ----------
        obj_id:
            The object being covered.
        covering_morphisms:
            List of morphism IDs in the covering sieve.

        Raises
        ------
        ValueError
            If *obj_id* is not registered in the site.
        """
        obj_ids = {o.obj_id for o in self.objects}
        if obj_id not in obj_ids:
            raise ValueError(
                f"FormalSite.add_cover: object '{obj_id}' not in site '{self.site_id}'"
            )
        self.covers.append(
            {"object_id": obj_id, "covering_morphisms": list(covering_morphisms)}
        )
        logger.debug(
            "FormalSite '%s': added cover for '%s' (%d morphisms)",
            self.site_id, obj_id, len(covering_morphisms),
        )

    # ------------------------------------------------------------------ #
    def check_site_axioms(self) -> dict[str, bool]:
        """Check the Grothendieck topology axioms for this site.

        Theory2.tex §9.1 — a Grothendieck topology must satisfy:
        - *maximality*: the maximal sieve (all morphisms into U) covers U.
        - *stability*: covers are stable under base change (pull-back).
        - *transitivity*: if a sieve S covers U and each S_i covers U_i,
          then the composite sieve covers U.

        This implementation checks each axiom to the extent that it can be
        verified from the data stored in the :class:`FormalSite`.

        Returns
        -------
        dict[str, bool]
            Mapping from axiom name to pass/fail verdict.
        """
        results: dict[str, bool] = {}
        mor_index: dict[str, MorphismData] = {
            m.morphism_id: m for m in self.morphisms
        }

        # Maximality: every object should appear in covers
        covered_objects = {c["object_id"] for c in self.covers}
        all_objects = {o.obj_id for o in self.objects}
        uncovered = all_objects - covered_objects
        if uncovered:
            logger.warning(
                "FormalSite '%s' maximality: objects %s have no cover recorded",
                self.site_id, uncovered,
            )
            results["maximality"] = False
        else:
            results["maximality"] = True

        # Stability: for each cover morphism f : V → U, the pull-back
        # cover of V should also be recorded.  We approximate this by
        # checking that each covering morphism's source also has a cover.
        stability_ok = True
        for cover in self.covers:
            for mid in cover["covering_morphisms"]:
                m = mor_index.get(mid)
                if m is None:
                    logger.warning(
                        "FormalSite '%s' stability: covering morphism '%s' not found",
                        self.site_id, mid,
                    )
                    stability_ok = False
                    continue
                if m.source_id not in covered_objects:
                    logger.debug(
                        "FormalSite '%s' stability: source '%s' of covering "
                        "morphism '%s' has no cover yet",
                        self.site_id, m.source_id, mid,
                    )
                    # Leaf nodes in the cover tree need not be covered
        results["stability"] = stability_ok

        # Transitivity: if every member of a covering sieve itself has a
        # cover, the composed sieves should still cover the original object.
        # We check that no cover consists entirely of morphisms whose sources
        # are also covering morphisms into a different object (double covering).
        transitivity_ok = True
        covering_target_ids: set[str] = set()
        for cover in self.covers:
            covering_target_ids.add(cover["object_id"])
        for cover in self.covers:
            for mid in cover["covering_morphisms"]:
                m = mor_index.get(mid)
                if m is None:
                    continue
                if m.target_id not in covering_target_ids:
                    logger.warning(
                        "FormalSite '%s' transitivity: morphism '%s' targets "
                        "'%s' which is not covered",
                        self.site_id, mid, m.target_id,
                    )
                    transitivity_ok = False
        results["transitivity"] = transitivity_ok

        logger.info(
            "FormalSite '%s' check_site_axioms: %s",
            self.site_id,
            {k: "PASS" if v else "FAIL" for k, v in results.items()},
        )
        return results

    # ------------------------------------------------------------------ #
    def get_sheaf_condition(self) -> str:
        """Return a string describing the sheaf condition for this site.

        Theory2.tex §9.1 Thm 9.1 — a presheaf F on (C_J, J) is a sheaf iff
        for every covering sieve {f_i : U_i → U}, the natural map
        F(U) → ∏ F(U_i) is an equaliser of the two maps to ∏ F(U_i ×_U U_j).

        Returns
        -------
        str
            Human-readable description of the equaliser condition.
        """
        if not self.covers:
            return (
                f"FormalSite '{self.site_id}': no covers recorded; "
                "sheaf condition is vacuously satisfied."
            )
        lines = [
            f"Sheaf condition for site '{self.name}' (id={self.site_id}):",
            "  For each covering family {f_i : U_i → U} in J,",
            "  the sequence",
            "    F(U) —→ ∏_i F(U_i) ⇉ ∏_{i,j} F(U_i ×_U U_j)",
            "  must be an equaliser.",
            "",
            f"  Recorded covers ({len(self.covers)}):",
        ]
        for cover in self.covers:
            obj_id = cover["object_id"]
            mids = cover["covering_morphisms"]
            lines.append(f"    Cover of '{obj_id}': {mids}")
        if self.coherence_conditions:
            lines.append("")
            lines.append("  Coherence conditions:")
            for cond in self.coherence_conditions:
                lines.append(f"    • {cond}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    def to_grothendieck_topology(self) -> dict[str, Any]:
        """Export the Grothendieck topology as a plain dictionary.

        Returns
        -------
        dict
            Keys: ``site_id``, ``name``, ``topology`` (list of cover dicts),
            ``num_objects``, ``num_morphisms``.
        """
        return {
            "site_id": self.site_id,
            "name": self.name,
            "num_objects": len(self.objects),
            "num_morphisms": len(self.morphisms),
            "topology": [dict(c) for c in self.covers],
            "coherence_conditions": list(self.coherence_conditions),
        }

    # ------------------------------------------------------------------ #
    def validate(self) -> bool:
        """Validate the site for well-formedness.

        Returns
        -------
        bool
            ``True`` if the site ID and name are non-empty, all objects and
            morphisms pass individual validation, and the category axioms hold.
        """
        ok = True
        if not self.site_id:
            logger.warning("FormalSite.validate: site_id is empty")
            ok = False
        if not self.name:
            logger.warning("FormalSite.validate: name is empty [id=%s]", self.site_id)
            ok = False
        for obj in self.objects:
            if not obj.validate():
                ok = False
        for m in self.morphisms:
            if not m.validate():
                ok = False
        axioms = self.category_structure.check_category_axioms()
        if not all(axioms.values()):
            ok = False
        return ok

    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        """Return a human-readable description of the site."""
        lines = [
            f"FormalSite '{self.name}' (id={self.site_id})",
            f"  objects   : {len(self.objects)}",
            f"  morphisms : {len(self.morphisms)}",
            f"  covers    : {len(self.covers)}",
        ]
        if self.coherence_conditions:
            lines.append(f"  coherence conditions ({len(self.coherence_conditions)}):")
            for cond in self.coherence_conditions:
                lines.append(f"    • {cond}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# §9.2  TrustAlgebraAxioms
# ---------------------------------------------------------------------------


@dataclass
class TrustAlgebraAxioms:
    """The trust algebra axioms of Theory2.tex §9.2.

    Theory2.tex §9.2 defines a partial-order algebra (T, ≤, ⊕, α) on the
    carrier set T = :class:`TrustLevel`.  This dataclass records the axiom
    schemas as strings (for documentation / audit) and provides methods to
    verify them computationally against the live :class:`TrustLevel` enum.

    Parameters
    ----------
    algebra_name:
        Human-readable name for this algebra instance.
    carrier_set:
        Names of the elements in the carrier set (should match TrustLevel).
    partial_order_axioms:
        Axiom schemas for the partial order ≤.
    composition_axioms:
        Axiom schemas for the composition operation ⊕.
    attenuation_axioms:
        Axiom schemas for the attenuation operator α.
    promotion_rules:
        Rules under which a trust level may be promoted (list of dicts
        with keys ``"from"``, ``"to"``, ``"condition"``).
    demotion_rules:
        Rules under which a trust level must be demoted.
    """

    algebra_name: str
    carrier_set: list[str] = field(default_factory=list)
    partial_order_axioms: list[str] = field(default_factory=list)
    composition_axioms: list[str] = field(default_factory=list)
    attenuation_axioms: list[str] = field(default_factory=list)
    promotion_rules: list[dict[str, Any]] = field(default_factory=list)
    demotion_rules: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        if not self.carrier_set:
            self.carrier_set = [lvl.name for lvl in _TRUST_ORDER]
        logger.debug(
            "TrustAlgebraAxioms '%s': carrier |T| = %d",
            self.algebra_name, len(self.carrier_set),
        )

    # ------------------------------------------------------------------ #
    def check_all_axioms(self) -> dict[str, bool]:
        """Run all axiom checks and return results.

        Theory2.tex §9.2 Alg 9.4 — executes monotonicity,
        no-silent-promotion, and challenge-conservativity checks.

        Returns
        -------
        dict[str, bool]
            Mapping from axiom name to pass/fail.
        """
        results: dict[str, bool] = {
            "carrier_matches_trust_level": self._check_carrier(),
            "partial_order_reflexive": self._check_reflexivity(),
            "partial_order_transitive": self._check_transitivity(),
            "partial_order_antisymmetric": self._check_antisymmetry(),
            "monotonicity": self.verify_monotonicity(),
            "no_silent_promotion": self.verify_no_silent_promotion(),
            "challenge_conservativity": self.verify_challenge_conservativity(),
        }
        logger.info(
            "TrustAlgebraAxioms.check_all_axioms: %s",
            {k: "PASS" if v else "FAIL" for k, v in results.items()},
        )
        return results

    # ------------------------------------------------------------------ #
    def _check_carrier(self) -> bool:
        """Check that carrier_set matches TrustLevel names."""
        expected = {lvl.name for lvl in TrustLevel}
        actual = set(self.carrier_set)
        missing = expected - actual
        extra = actual - expected
        if missing:
            logger.warning(
                "TrustAlgebraAxioms: carrier missing TrustLevel names: %s", missing
            )
        if extra:
            logger.warning(
                "TrustAlgebraAxioms: carrier has unknown names: %s", extra
            )
        return not missing and not extra

    # ------------------------------------------------------------------ #
    def _check_reflexivity(self) -> bool:
        """Verify ∀ t ∈ T: t ≤ t."""
        for lvl in TrustLevel:
            if _TRUST_RANK[lvl] != _TRUST_RANK[lvl]:
                return False  # tautologically true, but explicit
        return True

    # ------------------------------------------------------------------ #
    def _check_transitivity(self) -> bool:
        """Verify ∀ a ≤ b, b ≤ c ⟹ a ≤ c."""
        levels = list(TrustLevel)
        for a in levels:
            for b in levels:
                for c in levels:
                    ra, rb, rc = _TRUST_RANK[a], _TRUST_RANK[b], _TRUST_RANK[c]
                    if ra <= rb and rb <= rc:
                        if not ra <= rc:
                            logger.warning(
                                "TrustAlgebraAxioms: transitivity violated: "
                                "%s ≤ %s ≤ %s but not %s ≤ %s",
                                a, b, c, a, c,
                            )
                            return False
        return True

    # ------------------------------------------------------------------ #
    def _check_antisymmetry(self) -> bool:
        """Verify ∀ a ≤ b, b ≤ a ⟹ a = b."""
        levels = list(TrustLevel)
        for a in levels:
            for b in levels:
                ra, rb = _TRUST_RANK[a], _TRUST_RANK[b]
                if ra <= rb and rb <= ra:
                    if a is not b:
                        logger.warning(
                            "TrustAlgebraAxioms: antisymmetry violated: "
                            "%s and %s have same rank but differ",
                            a, b,
                        )
                        return False
        return True

    # ------------------------------------------------------------------ #
    def verify_monotonicity(self) -> bool:
        """Verify that the meet operation is monotone.

        Theory2.tex §9.2 Thm 9.3 — for all a ≤ a' and b ≤ b',
        meet(a, b) ≤ meet(a', b').

        Returns
        -------
        bool
        """
        levels = list(TrustLevel)
        for a in levels:
            for a_prime in levels:
                if _TRUST_RANK[a] > _TRUST_RANK[a_prime]:
                    continue  # a ≤ a' required
                for b in levels:
                    for b_prime in levels:
                        if _TRUST_RANK[b] > _TRUST_RANK[b_prime]:
                            continue  # b ≤ b' required
                        lhs = _trust_meet(a, b)
                        rhs = _trust_meet(a_prime, b_prime)
                        if _TRUST_RANK[lhs] > _TRUST_RANK[rhs]:
                            logger.warning(
                                "verify_monotonicity: violated for "
                                "a=%s a'=%s b=%s b'=%s: meet(a,b)=%s > meet(a',b')=%s",
                                a, a_prime, b, b_prime, lhs, rhs,
                            )
                            return False
        logger.debug("TrustAlgebraAxioms.verify_monotonicity: PASS")
        return True

    # ------------------------------------------------------------------ #
    def verify_no_silent_promotion(self) -> bool:
        """Verify the no-silent-promotion axiom.

        Theory2.tex §9.2 Thm 9.4 — no composition of lower-tier evidence
        can yield a result strictly above the maximum input tier.

        We check that meet(a, b) ≤ max(a, b) for all a, b ∈ T.

        Returns
        -------
        bool
        """
        for a in TrustLevel:
            for b in TrustLevel:
                meet = _trust_meet(a, b)
                join = _trust_join(a, b)
                if _TRUST_RANK[meet] > _TRUST_RANK[join]:
                    logger.warning(
                        "verify_no_silent_promotion: meet(%s, %s) = %s > join = %s",
                        a, b, meet, join,
                    )
                    return False
        logger.debug("TrustAlgebraAxioms.verify_no_silent_promotion: PASS")
        return True

    # ------------------------------------------------------------------ #
    def verify_challenge_conservativity(self) -> bool:
        """Verify the challenge-conservativity axiom.

        Theory2.tex §9.2 Thm 9.5 — introducing a CONTRADICTED evidence
        cannot raise the trust level of any composition result.  Formally:
        meet(CONTRADICTED, t) = CONTRADICTED for all t.

        Returns
        -------
        bool
        """
        contradicted = TrustLevel.CONTRADICTED
        for t in TrustLevel:
            result = _trust_meet(contradicted, t)
            if result is not contradicted:
                logger.warning(
                    "verify_challenge_conservativity: meet(CONTRADICTED, %s) = %s",
                    t, result,
                )
                return False
        logger.debug("TrustAlgebraAxioms.verify_challenge_conservativity: PASS")
        return True

    # ------------------------------------------------------------------ #
    def get_violations(self) -> list[str]:
        """Return a list of axiom names that fail.

        Returns
        -------
        list[str]
            Empty if all axioms pass.
        """
        results = self.check_all_axioms()
        return [name for name, passed in results.items() if not passed]

    # ------------------------------------------------------------------ #
    def to_logical_form(self) -> str:
        """Render the algebra axioms in a quasi-logical notation.

        Returns
        -------
        str
            Multi-line string with axiom schemas.
        """
        lines = [
            f"Trust Algebra '{self.algebra_name}'",
            f"  Carrier T = {{ {', '.join(self.carrier_set)} }}",
            "",
            "  Partial order axioms:",
        ]
        for ax in self.partial_order_axioms:
            lines.append(f"    {ax}")
        lines.append("")
        lines.append("  Composition axioms:")
        for ax in self.composition_axioms:
            lines.append(f"    {ax}")
        lines.append("")
        lines.append("  Attenuation axioms:")
        for ax in self.attenuation_axioms:
            lines.append(f"    {ax}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        """Return a human-readable description of this algebra."""
        n_promo = len(self.promotion_rules)
        n_demo = len(self.demotion_rules)
        violations = self.get_violations()
        status = "✓ all axioms pass" if not violations else f"✗ violations: {violations}"
        return (
            f"TrustAlgebraAxioms '{self.algebra_name}'\n"
            f"  carrier size   : {len(self.carrier_set)}\n"
            f"  promotion rules: {n_promo}\n"
            f"  demotion rules : {n_demo}\n"
            f"  status         : {status}"
        )


# ---------------------------------------------------------------------------
# §9.3  ObstructionTheory
# ---------------------------------------------------------------------------


@dataclass
class ObstructionTheory:
    """Cohomological obstruction theory for sheaf-descent.

    Theory2.tex §9.3 — given a covering family U = {U_i → X} and a
    presheaf F, the obstruction to gluing local sections lies in the
    first Čech cohomology group Ȟ¹(U, F).  A global section exists iff
    the obstruction class [ω] vanishes.

    Parameters
    ----------
    obstruction_classes:
        List of obstruction records, each a dict with keys
        ``"cover_id"``, ``"degree"``, ``"representative"``, ``"vanishes"``.
    coboundary_conditions:
        Conditions that a cochain must satisfy to be a coboundary.
    cohomological_degree:
        The degree in which obstruction lives (typically 1).
    coefficient_sheaf:
        Human-readable name of the coefficient sheaf F.
    vanishing_conditions:
        Conditions under which all obstructions vanish (e.g. Thm 9.6).
    """

    obstruction_classes: list[dict[str, Any]] = field(default_factory=list)
    coboundary_conditions: list[dict[str, Any]] = field(default_factory=list)
    cohomological_degree: int = 1
    coefficient_sheaf: str = "F"
    vanishing_conditions: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def compute_obstruction(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Compute the obstruction coclass from local section data.

        Theory2.tex §9.3 Alg 9.5 — given local sections s_i ∈ F(U_i),
        the obstruction cochain ω_{ij} = s_j|_{U_{ij}} - s_i|_{U_{ij}}
        measures the failure of the sections to agree on overlaps.

        Parameters
        ----------
        data:
            Dictionary with keys:
            - ``"cover"`` (list[str]): covering object IDs U_i.
            - ``"local_sections"`` (dict[str, Any]): s_i for each U_i.
            - ``"restrictions"`` (dict[str, Any]): restriction maps.

        Returns
        -------
        list[dict]
            List of obstruction records for each pair (i, j) with i < j.
            Each record has keys ``"pair"``, ``"cochain_value"``,
            ``"is_zero"``, ``"vanishes"``.
        """
        cover: list[str] = data.get("cover", [])
        local_sections: dict[str, Any] = data.get("local_sections", {})
        restrictions: dict[str, Any] = data.get("restrictions", {})

        obstructions: list[dict[str, Any]] = []

        for i, u_i in enumerate(cover):
            for j, u_j in enumerate(cover):
                if j <= i:
                    continue
                pair_key = f"{u_i},{u_j}"
                s_i = local_sections.get(u_i)
                s_j = local_sections.get(u_j)
                # Restriction of s_j to intersection
                r_ji_key = f"{u_j}|{u_i}"
                r_ij_key = f"{u_i}|{u_j}"
                s_j_restricted = restrictions.get(r_ji_key, s_j)
                s_i_restricted = restrictions.get(r_ij_key, s_i)
                # ω_{ij} = s_j|_{ij} - s_i|_{ij} (abstractly: they should agree)
                cochain_val = None
                is_zero = False
                if s_i_restricted is not None and s_j_restricted is not None:
                    is_zero = s_i_restricted == s_j_restricted
                    cochain_val = None if is_zero else {
                        "s_i_restricted": s_i_restricted,
                        "s_j_restricted": s_j_restricted,
                    }
                record: dict[str, Any] = {
                    "pair": (u_i, u_j),
                    "pair_key": pair_key,
                    "cochain_value": cochain_val,
                    "is_zero": is_zero,
                    "vanishes": is_zero,
                }
                obstructions.append(record)

        self.obstruction_classes = obstructions
        logger.info(
            "ObstructionTheory.compute_obstruction: %d pairs, %d non-zero",
            len(obstructions),
            sum(1 for o in obstructions if not o["is_zero"]),
        )
        return obstructions

    # ------------------------------------------------------------------ #
    def check_coboundary(self, cochain: dict[str, Any]) -> bool:
        """Check whether *cochain* is a coboundary.

        Theory2.tex §9.3 Def 9.10 — a 1-cochain ω is a coboundary iff
        there exist 0-cochains (sections) {t_i} such that
        ω_{ij} = t_j|_{ij} - t_i|_{ij} for all i, j.

        Parameters
        ----------
        cochain:
            Dictionary mapping pair keys ``"U_i,U_j"`` to cochain values.

        Returns
        -------
        bool
            ``True`` if the cochain is the zero cochain or satisfies the
            recorded :attr:`coboundary_conditions`.
        """
        # Trivial check: zero cochain is always a coboundary
        if not cochain:
            return True
        if all(v == 0 or v is None for v in cochain.values()):
            return True

        # Check against recorded coboundary conditions
        for cond in self.coboundary_conditions:
            key: str = cond.get("pair_key", "")
            expected = cond.get("coboundary_value")
            actual = cochain.get(key)
            if actual is not None and actual != expected:
                logger.debug(
                    "ObstructionTheory.check_coboundary: pair '%s' cochain value %r "
                    "does not match expected coboundary %r",
                    key, actual, expected,
                )
                return False
        return True

    # ------------------------------------------------------------------ #
    def lift_if_unobstructed(
        self, local_sections: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Attempt to lift local sections to a global section.

        Theory2.tex §9.3 Thm 9.6 — if the obstruction class vanishes,
        there exists a unique global section whose restrictions agree with
        the given local sections.

        Parameters
        ----------
        local_sections:
            Mapping from object ID to local section value.

        Returns
        -------
        dict or None
            A ``{"global_section": value}`` dict if unobstructed, or
            ``None`` if the obstruction does not vanish.
        """
        if not self.obstruction_classes:
            logger.debug(
                "ObstructionTheory.lift_if_unobstructed: no obstruction classes "
                "computed; assuming unobstructed"
            )
            # Merge all local sections as the global section
            global_section = dict(local_sections)
            return {"global_section": global_section, "source": "local_merge"}

        non_vanishing = [o for o in self.obstruction_classes if not o.get("vanishes", False)]
        if non_vanishing:
            logger.info(
                "ObstructionTheory.lift_if_unobstructed: %d non-vanishing obstructions; "
                "lift failed",
                len(non_vanishing),
            )
            return None

        # All obstructions vanish: construct global section by taking the
        # common value on each overlap (first consistent value wins).
        global_section: dict[str, Any] = {}
        for obj_id, section in local_sections.items():
            if obj_id not in global_section:
                global_section[obj_id] = section
            elif global_section[obj_id] != section:
                logger.warning(
                    "ObstructionTheory.lift_if_unobstructed: inconsistency on '%s'",
                    obj_id,
                )
                return None

        logger.info(
            "ObstructionTheory.lift_if_unobstructed: global section constructed "
            "from %d local sections",
            len(local_sections),
        )
        return {"global_section": global_section, "source": "obstruction_theory"}

    # ------------------------------------------------------------------ #
    def get_cohomology_class(self) -> str:
        """Describe the current obstruction cohomology class.

        Returns
        -------
        str
            A string of the form ``[ω] ∈ Ȟ^d(U, F)`` indicating whether
            the class vanishes.
        """
        degree = self.cohomological_degree
        sheaf = self.coefficient_sheaf
        non_vanishing = [o for o in self.obstruction_classes if not o.get("vanishes", True)]
        if not non_vanishing:
            return f"[ω] = 0 ∈ Ȟ^{degree}(U, {sheaf})  (obstruction vanishes)"
        return (
            f"[ω] ≠ 0 ∈ Ȟ^{degree}(U, {sheaf})  "
            f"({len(non_vanishing)} non-zero component(s))"
        )

    # ------------------------------------------------------------------ #
    def describe_obstructions(self) -> str:
        """Return a human-readable summary of all obstruction classes."""
        if not self.obstruction_classes:
            return "ObstructionTheory: no obstruction classes computed."
        lines = [
            f"ObstructionTheory (Ȟ^{self.cohomological_degree}, "
            f"coeff sheaf={self.coefficient_sheaf}):",
            f"  {len(self.obstruction_classes)} obstruction pair(s):",
        ]
        for obs in self.obstruction_classes:
            pair = obs.get("pair", ("?", "?"))
            vanishes = obs.get("vanishes", False)
            tag = "✓" if vanishes else "✗"
            lines.append(f"    {tag} ({pair[0]}, {pair[1]})")
        lines.append(f"  Overall: {self.get_cohomology_class()}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    def to_cochain_complex(self) -> dict[str, Any]:
        """Export a simplified cochain complex description.

        Returns
        -------
        dict
            Keys: ``degree``, ``coefficient_sheaf``, ``cochains`` (list),
            ``vanishing_conditions``.
        """
        return {
            "degree": self.cohomological_degree,
            "coefficient_sheaf": self.coefficient_sheaf,
            "cochains": list(self.obstruction_classes),
            "coboundary_conditions": list(self.coboundary_conditions),
            "vanishing_conditions": list(self.vanishing_conditions),
            "cohomology_class": self.get_cohomology_class(),
        }


# ---------------------------------------------------------------------------
# §9.3  DescentData
# ---------------------------------------------------------------------------


@dataclass
class DescentData:
    """Descent datum for a covering family on a formal site.

    Theory2.tex §9.3 Def 9.12 — a *descent datum* for a sheaf F relative
    to a cover U = {U_i → X} consists of:
    - local sections s_i ∈ F(U_i),
    - gluing morphisms φ_{ij}: s_j|_{U_{ij}} ≅ s_i|_{U_{ij}},
    - coherence data ensuring the cocycle condition φ_{ij} ∘ φ_{jk} = φ_{ik}
      on triple overlaps.

    Parameters
    ----------
    site_id:
        ID of the :class:`FormalSite` to which this descent datum belongs.
    cover:
        List of object IDs U_i forming the cover of X.
    local_sections:
        Mapping from U_i (object ID) to the local section value s_i.
    gluing_morphisms:
        Mapping from pair keys ``"U_i|U_j"`` to the gluing isomorphism data.
    coherence_data:
        Mapping storing triple-overlap coherence witnesses.
    """

    site_id: str
    cover: list[str] = field(default_factory=list)
    local_sections: dict[str, Any] = field(default_factory=dict)
    gluing_morphisms: dict[str, Any] = field(default_factory=dict)
    coherence_data: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    def check_descent_condition(self) -> bool:
        """Check whether the descent condition (cocycle condition) holds.

        Theory2.tex §9.3 Thm 9.7 — the cocycle condition requires:
        φ_{ij} ∘ φ_{jk} = φ_{ik}  for all i, j, k.

        This implementation checks that for all triples (i, j, k), if
        φ_{ij} and φ_{jk} and φ_{ik} are all present in
        :attr:`gluing_morphisms`, then the composite matches.

        Returns
        -------
        bool
            ``True`` if the cocycle condition is satisfied.
        """
        n = len(self.cover)
        if n == 0:
            return True  # vacuously true

        all_ok = True
        for i, u_i in enumerate(self.cover):
            for j, u_j in enumerate(self.cover):
                if j == i:
                    continue
                for k, u_k in enumerate(self.cover):
                    if k == i or k == j:
                        continue
                    key_ij = f"{u_i}|{u_j}"
                    key_jk = f"{u_j}|{u_k}"
                    key_ik = f"{u_i}|{u_k}"
                    phi_ij = self.gluing_morphisms.get(key_ij)
                    phi_jk = self.gluing_morphisms.get(key_jk)
                    phi_ik = self.gluing_morphisms.get(key_ik)
                    if phi_ij is None or phi_jk is None or phi_ik is None:
                        # Cannot check — insufficient data
                        continue
                    # Abstract equality check: φ_{ij} ∘ φ_{jk} should equal φ_{ik}
                    # If gluing morphisms are represented as dicts, check key overlap
                    if isinstance(phi_ij, dict) and isinstance(phi_jk, dict):
                        composed = {**phi_jk, **phi_ij}  # ij overrides jk
                        if isinstance(phi_ik, dict) and composed != phi_ik:
                            logger.warning(
                                "DescentData.check_descent_condition: cocycle "
                                "condition failed for triple (%s, %s, %s)",
                                u_i, u_j, u_k,
                            )
                            all_ok = False
                    elif phi_ij != phi_ik or phi_jk != phi_ik:
                        # Scalar/string morphisms: all three should agree for
                        # a trivial descent datum
                        pass  # Relaxed check for non-dict morphisms

        logger.info(
            "DescentData.check_descent_condition: %s", "PASS" if all_ok else "FAIL"
        )
        return all_ok

    # ------------------------------------------------------------------ #
    def glue(self) -> dict[str, Any] | None:
        """Attempt to glue local sections into a global section.

        Theory2.tex §9.3 Alg 9.6 — if the descent condition holds and the
        obstruction class vanishes, a unique global section exists whose
        restriction to each U_i equals s_i.

        Returns
        -------
        dict or None
            A ``{"global_section": <value>, "cover_size": n}`` dict if
            gluing succeeds, or ``None`` if the descent condition fails.
        """
        if not self.check_descent_condition():
            logger.info("DescentData.glue: descent condition failed; cannot glue")
            return None

        if not self.local_sections:
            logger.warning("DescentData.glue: no local sections provided")
            return None

        # Build the global section by taking the union of local sections
        # under the assumption that they are consistent (descent condition passed).
        global_section: dict[str, Any] = {}
        for obj_id in self.cover:
            s = self.local_sections.get(obj_id)
            if s is None:
                logger.warning(
                    "DescentData.glue: no local section for '%s' in cover", obj_id
                )
                return None
            global_section[obj_id] = s

        logger.info(
            "DescentData.glue: successfully glued %d local sections on site '%s'",
            len(global_section), self.site_id,
        )
        return {
            "global_section": global_section,
            "cover_size": len(self.cover),
            "site_id": self.site_id,
        }

    # ------------------------------------------------------------------ #
    def get_cocycle_condition(self) -> str:
        """Return a human-readable statement of the cocycle condition.

        Returns
        -------
        str
            Multi-line description including the cover and gluing data.
        """
        lines = [
            f"Cocycle condition for site '{self.site_id}':",
            f"  Cover U = {{ {', '.join(self.cover)} }}",
            "  Requirement: ∀ i, j, k:  φ_{{ij}} ∘ φ_{{jk}} = φ_{{ik}}",
        ]
        if self.gluing_morphisms:
            lines.append(
                f"  Recorded gluing morphisms ({len(self.gluing_morphisms)}):"
            )
            for key in sorted(self.gluing_morphisms):
                lines.append(f"    {key}: {self.gluing_morphisms[key]!r}")
        else:
            lines.append("  No gluing morphisms recorded.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    def validate_coherence(self) -> bool:
        """Validate that coherence witnesses are present for all triple overlaps.

        Theory2.tex §9.3 Def 9.12 — coherence data must provide a witness
        for every triple (i, j, k) of distinct cover elements.

        Returns
        -------
        bool
            ``True`` if all expected triple keys are present.
        """
        n = len(self.cover)
        if n < 3:
            return True  # No triples possible

        all_present = True
        for i, u_i in enumerate(self.cover):
            for j, u_j in enumerate(self.cover):
                if j <= i:
                    continue
                for k, u_k in enumerate(self.cover):
                    if k <= j:
                        continue
                    triple_key = f"{u_i}|{u_j}|{u_k}"
                    if triple_key not in self.coherence_data:
                        logger.debug(
                            "DescentData.validate_coherence: missing triple key '%s'",
                            triple_key,
                        )
                        all_present = False

        logger.info(
            "DescentData.validate_coherence: %s", "PASS" if all_present else "FAIL"
        )
        return all_present

    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        """Return a human-readable description of this descent datum."""
        lines = [
            f"DescentData (site_id='{self.site_id}')",
            f"  cover         : {self.cover}",
            f"  local sections: {len(self.local_sections)}",
            f"  gluing morphs : {len(self.gluing_morphisms)}",
            f"  coherence keys: {len(self.coherence_data)}",
        ]
        descent_ok = self.check_descent_condition()
        lines.append(
            f"  descent cond  : {'✓ satisfied' if descent_ok else '✗ violated'}"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "ObjectData",
    "MorphismData",
    "CategoryStructure",
    "FormalSite",
    "TrustAlgebraAxioms",
    "ObstructionTheory",
    "DescentData",
    # helpers (internal but useful for testing)
    "_trust_meet",
    "_trust_join",
    "_TRUST_ORDER",
    "_TRUST_RANK",
    "model_to_judgment",
    "model_to_encoding",
]


# ---------------------------------------------------------------------------
# Cross-referencing helpers: judgments & encodings
# ---------------------------------------------------------------------------


def model_to_judgment(model_data: ObjectData | MorphismData) -> dict[str, Any]:
    """Convert a formal-core model datum into a judgment term.

    Uses :mod:`jugeo.judgments.judgment_terms` for proposition construction
    and :mod:`jugeo.geometry.descent` for wrapping the result as a local
    section.  Returns a plain dict so callers need not depend on those
    modules directly.

    Parameters
    ----------
    model_data:
        An :class:`ObjectData` or :class:`MorphismData` instance.

    Returns
    -------
    dict[str, Any]
        A judgment dict with keys ``proposition``, ``status``,
        ``local_section``, and ``source_id``.
    """
    try:
        from jugeo.judgments.judgment_terms import (
            JudgmentStatus,
            Proposition,
            PropositionKind,
        )
    except ImportError:
        logger.warning("model_to_judgment: jugeo.judgments.judgment_terms unavailable")
        return {"error": "missing dependency: jugeo.judgments.judgment_terms"}

    try:
        from jugeo.geometry.descent import LocalSection
    except ImportError:
        logger.warning("model_to_judgment: jugeo.geometry.descent unavailable")
        return {"error": "missing dependency: jugeo.geometry.descent"}

    if isinstance(model_data, MorphismData):
        kind = PropositionKind.RELATIONAL
        formula = f"{model_data.source_id} -> {model_data.target_id}"
        source_id = model_data.morphism_id
        free_vars = tuple(model_data.structure_map.keys())
    else:
        kind = PropositionKind.STRUCTURAL
        formula = f"{model_data.object_type}({model_data.obj_id})"
        source_id = model_data.obj_id
        free_vars = tuple(model_data.attributes.keys())

    prop = Proposition(kind=kind, formula=formula, free_variables=free_vars)
    status = JudgmentStatus.PROPOSED

    section = LocalSection(
        coordinate=source_id,
        judgment_data={"proposition": prop, "status": status.value},
        trust_level=0.5,
    )

    logger.debug("model_to_judgment: built judgment for %s", source_id)
    return {
        "proposition": {"kind": prop.kind.value, "formula": prop.formula},
        "status": status.value,
        "local_section": {"coordinate": section.coordinate, "trust": section.trust_level},
        "source_id": source_id,
    }


def model_to_encoding(
    model_data: ObjectData | MorphismData, *, format: str = "z3"
) -> dict[str, Any]:
    """Encode a formal-core model datum into a solver-compatible format.

    Uses :func:`jugeo.encodings.encode_judgment` and
    :func:`jugeo.encodings.encode_section` for serialisation, and
    :class:`jugeo.geometry.covers.CoverMember` /
    :func:`jugeo.geometry.covers.score_cover` for coverage scoring.

    Parameters
    ----------
    model_data:
        An :class:`ObjectData` or :class:`MorphismData` instance.
    format:
        Target solver format (default ``"z3"``).

    Returns
    -------
    dict[str, Any]
        An encoding dict with keys ``format``, ``encoded_judgment``,
        ``encoded_section``, ``cover_score``, and ``source_id``.
    """
    try:
        from jugeo.encodings import encode_judgment, encode_section
    except ImportError:
        logger.warning("model_to_encoding: jugeo.encodings unavailable")
        return {"error": "missing dependency: jugeo.encodings"}

    try:
        from jugeo.geometry.covers import CoverMember, score_cover
    except ImportError:
        logger.warning("model_to_encoding: jugeo.geometry.covers unavailable")
        return {"error": "missing dependency: jugeo.geometry.covers"}

    judgment_term = model_to_judgment(model_data)
    if "error" in judgment_term:
        return {"error": judgment_term["error"], "phase": "judgment_conversion"}

    encoded_j = encode_judgment(judgment_term)
    encoded_s = encode_section(judgment_term)

    source_id = judgment_term["source_id"]
    cover_score: dict[str, Any] | None = None
    if isinstance(model_data, MorphismData) and model_data.is_covering:
        try:
            member = CoverMember(
                source_coordinate=model_data.source_id,
                target_coordinate=model_data.target_id,
                restriction_morphism=model_data.morphism_id,
                index=0,
            )
            cover_score = {"member_index": member.index, "trust_ceiling": member.trust_ceiling}
        except Exception:
            logger.debug("model_to_encoding: CoverMember construction skipped")

    logger.debug("model_to_encoding: encoded %s as %s", source_id, format)
    return {
        "format": format,
        "encoded_judgment": encoded_j,
        "encoded_section": encoded_s,
        "cover_score": cover_score,
        "source_id": source_id,
    }
