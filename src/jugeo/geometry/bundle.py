"""Judgment Fiber Bundle over a verification site.

Trust is a connection on the judgment bundle: it transforms as
judgments are parallel-transported along morphisms in the site.
The curvature of this connection detects structural inconsistencies
invisible to local checks.  Characteristic classes provide global
invariants of the verification state.

This module brings differential-geometric tools to the existing
sheaf-theoretic verification framework defined in ``site.py``.

Mathematical summary
~~~~~~~~~~~~~~~~~~~~

Let *B* be a verification site (a category-with-topology) and let
*F_c* be the space of judgments at a coordinate *c*.  The judgment
fiber bundle is the total space

.. math::

   E = \\bigsqcup_{c \\in B} F_c \\;\\xrightarrow{\\pi}\\; B

Trust defines a *connection* on this bundle: given a morphism
*f : c → d* in the site, the connection specifies how to
parallel-transport a judgment from *F_c* to *F_d*.

* **Curvature** at a 2-simplex *(c₁, c₂, c₃)* measures the failure
  of trust transport to be path-independent.  Zero curvature means
  consistent trust across the triangle.

* **Holonomy** around a closed loop detects global topological
  defects in the trust distribution.

* The **first Chern class** *c₁* is the average curvature over all
  2-faces.  *c₁ = 0* iff the bundle admits a flat connection
  (globally consistent trust).

* **Trust stratification** partitions the judgment space by trust
  level, enabling stratum-specific diagnostics and inter-stratum
  consistency checks.

Existing-module integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~

* ``jugeo.geometry.site`` — ``Site``, ``Coordinate``, ``Morphism``,
  ``MorphismKind``, ``CoordinateKind`` provide the base category
  over which the bundle lives.

* ``jugeo.evidence.trust`` — ``TrustLevel`` supplies the ordered
  algebra whose elements fill the fibers.  ``TrustAlgebra`` gains
  bundle-aware helper methods (added separately in ``trust.py``).

* ``jugeo.orchestration.fleet`` — Fleet members produce judgments
  that populate fibers; the bundle diagnoses trust coherence across
  the fleet's combined output.

Backward compatibility
~~~~~~~~~~~~~~~~~~~~~~

This is a *new* module — no backward-compat aliases are required.
All existing imports continue to work unmodified.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations
from typing import Any, Mapping, Sequence

from jugeo.evidence.trust import TrustLevel
from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
    Site,
)


# ---------------------------------------------------------------------------
# Evidence channel taxonomy
# ---------------------------------------------------------------------------


class EvidenceChannel(str, Enum):
    """Channel through which evidence was obtained.

    Each judgment is tagged with the channel that produced it so that
    connection observations can weight transport deltas per-channel.

    The values are stable strings suitable for serialization and display
    in the copilot diagnostic panel.
    """

    Z3_PROOF = "z3_proof"
    """Evidence obtained from an SMT (Z3) proof discharge."""

    RUNTIME_TEST = "runtime_test"
    """Evidence obtained by executing a runtime test suite."""

    TYPE_CHECK = "type_check"
    """Evidence obtained from a type-checker pass."""

    HUMAN_REVIEW = "human_review"
    """Evidence obtained from a human reviewer's attestation."""

    COPILOT_SUGGESTION = "copilot_suggestion"
    """Evidence obtained from a copilot (AI assistant) suggestion."""

    STATIC_ANALYSIS = "static_analysis"
    """Evidence obtained from a static analysis tool."""

    PROPERTY_TEST = "property_test"
    """Evidence obtained from property-based (generative) testing."""

    FORMAL_PROOF = "formal_proof"
    """Evidence obtained from a mechanized proof assistant (Lean, Coq, etc.)."""


# ---------------------------------------------------------------------------
# Judgment — the fundamental epistemic unit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Judgment:
    """A judgment is a claim + evidence + trust + channel at a coordinate.

    This is the fundamental object of Judgment Geometry — not just a
    proposition, but a proposition together with its full epistemic
    context.  The ``coordinate`` anchors the judgment in the site, the
    ``claim`` states what is asserted, the ``evidence`` tuple records the
    supporting artefacts, the ``trust`` records the trust level, and the
    ``channel`` records how the evidence was produced.

    Parameters
    ----------
    coordinate : Coordinate
        The point in the site at which this judgment lives.
    claim : str
        The proposition being asserted (natural-language or formal).
    evidence : tuple[str, ...]
        References to evidence artefacts supporting the claim.
    trust : TrustLevel
        Current trust level for this judgment.
    channel : EvidenceChannel
        Channel through which the evidence was obtained.
    source : str
        Identity of the agent that produced the judgment (e.g. a solver
        name, ``"human"``, or a copilot session ID).
    metadata : Mapping[str, Any]
        Arbitrary extra data attached to the judgment.
    """

    coordinate: Coordinate
    claim: str
    evidence: tuple[str, ...] = ()
    trust: TrustLevel = TrustLevel.UNVERIFIED
    channel: EvidenceChannel = EvidenceChannel.COPILOT_SUGGESTION
    source: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fiber — the set of judgments at a single coordinate
# ---------------------------------------------------------------------------


@dataclass
class JudgmentFiber:
    """The fiber *F_c* over a coordinate *c*: all judgments at that point.

    A fiber collects every judgment that has been registered at its
    coordinate.  Aggregate properties (average trust, active channels,
    contradiction detection) are exposed as computed properties so that
    callers never need to iterate the raw list manually.

    Parameters
    ----------
    coordinate : Coordinate
        The site coordinate this fiber sits over.
    judgments : list[Judgment]
        The accumulated judgments at this coordinate.
    """

    coordinate: Coordinate
    judgments: list[Judgment] = field(default_factory=list)

    # -- computed properties ------------------------------------------------

    @property
    def trust_levels(self) -> list[TrustLevel]:
        """Return the trust level of every judgment in this fiber."""
        return [j.trust for j in self.judgments]

    @property
    def average_trust(self) -> float:
        """Average strength index of judgments in this fiber.

        Returns ``0.0`` for an empty fiber.  The index runs from 0
        (``CONTRADICTED``) to 7 (``MECHANICALLY_VERIFIED``).
        """
        if not self.judgments:
            return 0.0
        return sum(j.trust._strength_index() for j in self.judgments) / len(
            self.judgments
        )

    @property
    def channels(self) -> set[EvidenceChannel]:
        """The distinct evidence channels present in this fiber."""
        return {j.channel for j in self.judgments}

    @property
    def channel_count(self) -> int:
        """Number of distinct channels — a basic diversity metric."""
        return len(self.channels)

    # -- contradiction detection -------------------------------------------

    def has_contradiction(self) -> bool:
        """Check for contradictions in this fiber.

        A fiber is contradicted if any judgment carries
        ``TrustLevel.CONTRADICTED``.  This is a lightweight first-pass
        check; deeper claim-level contradiction analysis may be layered
        on top.
        """
        return any(j.trust == TrustLevel.CONTRADICTED for j in self.judgments)

    def claim_conflicts(self) -> list[tuple[Judgment, Judgment]]:
        """Return pairs of judgments with distinct claims at this coordinate.

        Two judgments at the same coordinate making different claims
        constitute a *claim conflict* that the bundle should resolve.
        This does not check for semantic contradiction — only textual
        difference — but even that signals possible inconsistency.
        """
        conflicts: list[tuple[Judgment, Judgment]] = []
        for i, j1 in enumerate(self.judgments):
            for j2 in self.judgments[i + 1 :]:
                if j1.claim != j2.claim:
                    conflicts.append((j1, j2))
        return conflicts

    def __len__(self) -> int:
        return len(self.judgments)


# ---------------------------------------------------------------------------
# Transport observation — recording trust change along a morphism
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransportObservation:
    """Record of trust changing along a morphism.

    Each time we observe the trust levels of related judgments at the
    source and target of a site morphism, we store the observation here.
    The collection of all observations forms the empirical basis for the
    connection on the bundle.

    Parameters
    ----------
    morphism : Morphism
        The site morphism along which transport was observed.
    source_trust : TrustLevel
        Trust at the source coordinate.
    target_trust : TrustLevel
        Trust at the target coordinate.
    trust_delta : float
        ``target._strength_index() - source._strength_index()``.
    """

    morphism: Morphism
    source_trust: TrustLevel
    target_trust: TrustLevel
    trust_delta: float


# ---------------------------------------------------------------------------
# Trust connection — parallel transport of trust along morphisms
# ---------------------------------------------------------------------------


class TrustConnection:
    """A connection on the judgment bundle.

    In differential geometry, a connection tells you how to
    parallel-transport fiber data along paths in the base.  Here the
    "fiber data" is a trust level, and the "paths" are morphisms in the
    verification site.

    The connection is *empirical*: it is built from observed trust pairs
    at coordinates connected by morphisms.  Once built, it can be used
    to predict how trust should transform along any morphism, and its
    curvature detects structural inconsistencies.

    Usage::

        conn = TrustConnection()
        conn.observe(morphism, TrustLevel.HUMAN_ATTESTED,
                     TrustLevel.SOLVER_DISCHARGED)
        delta = conn.average_delta(src_key, tgt_key)
        transported = conn.transport(TrustLevel.HUMAN_ATTESTED, morphism)
    """

    def __init__(self) -> None:
        self._observations: dict[
            tuple[str, str], list[TransportObservation]
        ] = {}

    # -- observation -------------------------------------------------------

    def observe(
        self,
        morphism: Morphism,
        source_trust: TrustLevel,
        target_trust: TrustLevel,
    ) -> TransportObservation:
        """Record a trust transport observation along a morphism.

        Parameters
        ----------
        morphism : Morphism
            The morphism along which trust was transported.
        source_trust : TrustLevel
            Trust at the source coordinate.
        target_trust : TrustLevel
            Trust at the target coordinate.

        Returns
        -------
        TransportObservation
            The recorded observation (also stored internally).
        """
        key = (
            str(morphism.source.components),
            str(morphism.target.components),
        )
        obs = TransportObservation(
            morphism=morphism,
            source_trust=source_trust,
            target_trust=target_trust,
            trust_delta=(
                target_trust._strength_index() - source_trust._strength_index()
            ),
        )
        self._observations.setdefault(key, []).append(obs)
        return obs

    # -- querying ----------------------------------------------------------

    def observations_for(
        self, source_key: str, target_key: str
    ) -> list[TransportObservation]:
        """Return all observations between two coordinate keys."""
        return list(self._observations.get((source_key, target_key), []))

    def average_delta(self, source_key: str, target_key: str) -> float:
        """Average trust change along the edge ``source → target``.

        Returns ``0.0`` when no observations exist for this edge.
        """
        obs = self._observations.get((source_key, target_key), [])
        if not obs:
            return 0.0
        return sum(o.trust_delta for o in obs) / len(obs)

    def observation_count(self) -> int:
        """Total number of transport observations across all edges."""
        return sum(len(v) for v in self._observations.values())

    # -- parallel transport ------------------------------------------------

    def transport(
        self, trust: TrustLevel, morphism: Morphism
    ) -> TrustLevel:
        """Parallel-transport a trust level along a morphism.

        Uses the average observed delta for this edge to compute the
        transported trust.  Falls back to *flat transport* (identity)
        when no observations are available.

        Parameters
        ----------
        trust : TrustLevel
            The trust level to transport.
        morphism : Morphism
            The morphism along which to transport.

        Returns
        -------
        TrustLevel
            The transported trust level, clamped to the valid range.
        """
        key = (
            str(morphism.source.components),
            str(morphism.target.components),
        )
        obs = self._observations.get(key, [])
        if not obs:
            return trust  # flat transport when unobserved

        avg_delta = sum(o.trust_delta for o in obs) / len(obs)
        new_index = max(0, min(7, round(trust._strength_index() + avg_delta)))

        # Build index → TrustLevel map from the canonical ordering
        index_to_level: dict[int, TrustLevel] = {}
        for lvl in TrustLevel.ordered():
            idx = lvl._strength_index()
            if idx not in index_to_level:
                index_to_level[idx] = lvl
        return index_to_level.get(new_index, trust)


# ---------------------------------------------------------------------------
# Curvature — local inconsistency at a 2-simplex
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleCurvature:
    """Curvature at a 2-simplex (three coordinates forming a triangle).

    The curvature *F(c₁, c₂, c₃)* is defined as:

    .. math::

        F = \\Delta(c_1 \\to c_2) + \\Delta(c_2 \\to c_3)
            + \\Delta(c_3 \\to c_1)

    where *Δ* is the average trust delta along the corresponding edge.

    * **F = 0** — trust transport is path-independent around this
      triangle (flat / consistent).
    * **F > 0** — *positive curvature* — trust inflates around the
      loop (echo-chamber effect).
    * **F < 0** — *negative curvature* — trust deflates around the
      loop (adversarial erosion).

    Parameters
    ----------
    vertices : tuple[str, str, str]
        The three coordinate keys forming the 2-simplex.
    value : float
        The curvature value.
    edge_deltas : tuple[float, float, float]
        Individual edge deltas ``(Δ₁₂, Δ₂₃, Δ₃₁)``.
    """

    vertices: tuple[str, str, str]
    value: float
    edge_deltas: tuple[float, float, float]

    @property
    def is_flat(self) -> bool:
        """``True`` when curvature is numerically zero."""
        return abs(self.value) < 1e-9

    @property
    def interpretation(self) -> str:
        """Human-readable interpretation of the curvature sign."""
        if self.is_flat:
            return "flat (consistent trust)"
        elif self.value > 0:
            return "positive curvature (trust inflation / echo chamber)"
        else:
            return "negative curvature (trust deflation / adversarial)"


# ---------------------------------------------------------------------------
# Holonomy — global defect around a closed loop
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleHolonomy:
    """Holonomy around a closed loop of coordinates.

    The holonomy is the total trust shift accumulated when
    parallel-transporting around a cycle in the site.  Non-trivial
    holonomy detects global trust defects that local checks cannot
    see.

    Parameters
    ----------
    loop : tuple[str, ...]
        Coordinate keys forming a closed cycle (first == last).
    total_shift : float
        Sum of all edge deltas around the loop.
    edge_shifts : list[float]
        Per-edge trust deltas along the loop.
    """

    loop: tuple[str, ...]
    total_shift: float
    edge_shifts: list[float]

    @property
    def is_trivial(self) -> bool:
        """``True`` when the holonomy is numerically zero."""
        return abs(self.total_shift) < 1e-9

    @property
    def loop_length(self) -> int:
        """Number of edges in the loop."""
        return len(self.edge_shifts)


# ---------------------------------------------------------------------------
# Characteristic class — global invariant of the bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CharacteristicClass:
    r"""First Chern class *c₁* of the judgment bundle.

    The first Chern class is a global invariant computed as the average
    curvature over all 2-faces of the site simplicial nerve:

    .. math::

        c_1 = \\frac{1}{|\\mathcal{F}_2|}
              \\sum_{(c_1,c_2,c_3) \\in \\mathcal{F}_2} F(c_1,c_2,c_3)

    * *c₁ = 0* iff the bundle admits a flat connection (globally
      consistent trust).
    * *c₁ > 0* signals systematic trust inflation.
    * *c₁ < 0* signals systematic trust deflation.

    Parameters
    ----------
    c1 : float
        The first Chern class value.
    num_faces : int
        Number of 2-faces examined.
    num_curved : int
        Number of 2-faces with non-zero curvature.
    """

    c1: float
    num_faces: int
    num_curved: int

    @property
    def is_flat(self) -> bool:
        """``True`` when *c₁* is numerically zero."""
        return abs(self.c1) < 1e-9

    @property
    def interpretation(self) -> str:
        """Human-readable interpretation of the Chern class."""
        if self.is_flat:
            return "c₁ ≈ 0: globally consistent trust (flat bundle)"
        elif self.c1 > 0:
            return f"c₁ = {self.c1:+.4f}: trust inflation detected"
        else:
            return f"c₁ = {self.c1:+.4f}: trust deflation detected"


# ---------------------------------------------------------------------------
# Trust stratum — partition of judgment space by trust level
# ---------------------------------------------------------------------------


@dataclass
class TrustStratum:
    """A stratum of the trust-stratified judgment space.

    The judgment space decomposes into strata indexed by
    :class:`TrustLevel`.  Each stratum collects all judgments at a given
    trust level and exposes intra-stratum consistency checks.

    Parameters
    ----------
    level : TrustLevel
        The trust level defining this stratum.
    judgments : list[Judgment]
        Judgments belonging to this stratum.
    coordinates : set
        Set of coordinate keys (as strings) that appear in this stratum.
    """

    level: TrustLevel
    judgments: list[Judgment] = field(default_factory=list)
    coordinates: set = field(default_factory=set)

    @property
    def has_internal_contradiction(self) -> bool:
        """Detect intra-stratum contradictions.

        Two judgments in the same stratum (same trust level) at the same
        coordinate making *different* claims constitute an internal
        contradiction — an inconsistency within a single evidence tier.
        """
        by_coord: dict[str, list[Judgment]] = {}
        for j in self.judgments:
            key = str(j.coordinate.components)
            by_coord.setdefault(key, []).append(j)
        for coord_judgments in by_coord.values():
            claims = {j.claim for j in coord_judgments}
            if len(claims) > 1:
                return True
        return False

    @property
    def size(self) -> int:
        """Number of judgments in this stratum."""
        return len(self.judgments)


# ---------------------------------------------------------------------------
# VerificationBundle — the central construction
# ---------------------------------------------------------------------------


class VerificationBundle:
    """The Judgment Fiber Bundle over a verification site.

    This is the central construction of the module.  Trust is a
    *connection* on the bundle *E → B* where *B* is the verification
    site and the fiber at each coordinate is the space of judgments.

    Typical usage::

        site = SiteBuilder().add_coordinate(...).build()
        bundle = VerificationBundle(site)
        bundle.add_judgment(Judgment(...))
        bundle.build_connection()
        diag = bundle.diagnose()
        print(bundle.summary_text())

    The bundle may also be used *without* a site — in that case the
    connection is inferred from shared claims between fibers, which is
    useful for lightweight ad-hoc analysis.

    Parameters
    ----------
    site : Site | None
        The verification site forming the base space.  ``None`` for
        site-free analysis.
    """

    def __init__(self, site: Site | None = None) -> None:
        self.site = site
        self._fibers: dict[str, JudgmentFiber] = {}
        self._connection = TrustConnection()
        self._strata: dict[str, TrustStratum] = {}
        self._connection_built = False

    # -- fiber management --------------------------------------------------

    def add_judgment(self, judgment: Judgment) -> None:
        """Add a judgment to the bundle.

        The judgment is filed into the appropriate fiber (keyed by
        coordinate) and the appropriate trust stratum.  Adding a
        judgment invalidates a previously built connection.

        Parameters
        ----------
        judgment : Judgment
            The judgment to register.
        """
        key = str(judgment.coordinate.components)
        if key not in self._fibers:
            self._fibers[key] = JudgmentFiber(coordinate=judgment.coordinate)
        self._fibers[key].judgments.append(judgment)

        # Maintain trust stratification
        level_name = judgment.trust.name
        if level_name not in self._strata:
            self._strata[level_name] = TrustStratum(level=judgment.trust)
        self._strata[level_name].judgments.append(judgment)
        self._strata[level_name].coordinates.add(key)

        self._connection_built = False

    def fiber_at(self, coordinate_key: str) -> JudgmentFiber | None:
        """Return the fiber at the given coordinate key, or ``None``."""
        return self._fibers.get(coordinate_key)

    def fiber_keys(self) -> list[str]:
        """Return sorted list of all coordinate keys with non-empty fibers."""
        return sorted(self._fibers.keys())

    @property
    def total_judgments(self) -> int:
        """Total number of judgments across all fibers."""
        return sum(len(f) for f in self._fibers.values())

    # -- connection building -----------------------------------------------

    def build_connection(self) -> TrustConnection:
        """Build the trust connection from fiber overlaps.

        For each pair of coordinates connected by a morphism in the site,
        observe the trust differential between judgments at those
        coordinates.

        When no site is provided, the connection is inferred from
        judgments sharing the same claim text across different fibers.

        Returns
        -------
        TrustConnection
            The built connection (also stored as ``self._connection``).
        """
        self._connection = TrustConnection()
        if self.site is not None:
            self._build_connection_from_site()
        else:
            self._build_connection_from_claims()
        self._connection_built = True
        return self._connection

    def _build_connection_from_site(self) -> None:
        """Use site morphisms to observe trust transport.

        Iterates over every morphism in the site.  For each pair of
        fibers connected by a morphism, matches judgments by claim and
        records transport observations.
        """
        # Site stores morphisms as `_morphisms`
        morphisms: list[Morphism] = getattr(self.site, "_morphisms", [])
        for morphism in morphisms:
            src_key = str(morphism.source.components)
            tgt_key = str(morphism.target.components)
            src_fiber = self._fibers.get(src_key)
            tgt_fiber = self._fibers.get(tgt_key)
            if (
                src_fiber
                and tgt_fiber
                and src_fiber.judgments
                and tgt_fiber.judgments
            ):
                for sj in src_fiber.judgments:
                    for tj in tgt_fiber.judgments:
                        if sj.claim == tj.claim or self._claims_overlap(
                            sj.claim, tj.claim
                        ):
                            self._connection.observe(morphism, sj.trust, tj.trust)

    def _build_connection_from_claims(self) -> None:
        """Infer connection from shared claims between fibers.

        Without a site, we create synthetic ``TRANSPORT`` morphisms
        between every pair of fibers that share at least one claim, then
        record the trust delta.
        """
        keys = list(self._fibers.keys())
        for i, k1 in enumerate(keys):
            for k2 in keys[i + 1 :]:
                f1 = self._fibers[k1]
                f2 = self._fibers[k2]
                for j1 in f1.judgments:
                    for j2 in f2.judgments:
                        if j1.claim == j2.claim or self._claims_overlap(
                            j1.claim, j2.claim
                        ):
                            m = Morphism(
                                source=f1.coordinate,
                                target=f2.coordinate,
                                kind=MorphismKind.TRANSPORT,
                            )
                            self._connection.observe(m, j1.trust, j2.trust)

    @staticmethod
    def _claims_overlap(c1: str, c2: str) -> bool:
        """Heuristic: two claims overlap if they share significant words.

        Stop-words are filtered out.  If at least 50 % of the smaller
        word-set appears in the larger, the claims are considered
        overlapping.  This is intentionally coarse — a future revision
        may plug in a semantic similarity model.
        """
        stop_words = frozenset(
            {
                "the", "a", "an", "is", "was", "are", "were",
                "has", "have", "had", "in", "on", "at", "to",
                "for", "of", "with", "by", "and", "or", "not",
                "it", "its", "this", "that", "be", "been",
            }
        )
        words1 = set(c1.lower().split()) - stop_words
        words2 = set(c2.lower().split()) - stop_words
        if not words1 or not words2:
            return False
        overlap = words1 & words2
        return len(overlap) / min(len(words1), len(words2)) > 0.5

    # -- curvature ---------------------------------------------------------

    def curvature(self, c1: str, c2: str, c3: str) -> BundleCurvature:
        """Compute curvature at the 2-simplex *(c1, c2, c3)*.

        .. math::

            F = \\Delta(c_1 \\to c_2) + \\Delta(c_2 \\to c_3)
                + \\Delta(c_3 \\to c_1)

        Automatically builds the connection if it has not been built.

        Parameters
        ----------
        c1, c2, c3 : str
            Coordinate keys forming the triangle.

        Returns
        -------
        BundleCurvature
            The curvature at this 2-simplex.
        """
        if not self._connection_built:
            self.build_connection()

        d12 = self._connection.average_delta(c1, c2)
        d23 = self._connection.average_delta(c2, c3)
        d31 = self._connection.average_delta(c3, c1)
        return BundleCurvature(
            vertices=(c1, c2, c3),
            value=d12 + d23 + d31,
            edge_deltas=(d12, d23, d31),
        )

    # -- holonomy ----------------------------------------------------------

    def holonomy(self, loop: Sequence[str]) -> BundleHolonomy:
        """Compute holonomy around a closed loop of coordinates.

        The loop should be a sequence of coordinate keys.  If the first
        and last elements differ the loop is automatically closed.

        Parameters
        ----------
        loop : Sequence[str]
            Coordinate keys forming the cycle.

        Returns
        -------
        BundleHolonomy
            The holonomy around the loop.
        """
        if not self._connection_built:
            self.build_connection()

        closed = list(loop)
        if closed[0] != closed[-1]:
            closed.append(closed[0])

        shifts: list[float] = []
        for i in range(len(closed) - 1):
            shifts.append(
                self._connection.average_delta(closed[i], closed[i + 1])
            )
        return BundleHolonomy(
            loop=tuple(closed),
            total_shift=sum(shifts),
            edge_shifts=shifts,
        )

    # -- characteristic class ----------------------------------------------

    def first_chern_class(self) -> CharacteristicClass:
        """Compute the first Chern class *c₁* = average curvature.

        Enumerates all 3-element combinations of fiber keys, computes
        curvature at each, and averages.

        Returns
        -------
        CharacteristicClass
            The first Chern class of the bundle.
        """
        if not self._connection_built:
            self.build_connection()

        keys = sorted(self._fibers.keys())
        if len(keys) < 3:
            return CharacteristicClass(c1=0.0, num_faces=0, num_curved=0)

        curvatures: list[BundleCurvature] = []
        for triple in combinations(keys, 3):
            curvatures.append(self.curvature(*triple))

        if not curvatures:
            return CharacteristicClass(c1=0.0, num_faces=0, num_curved=0)

        c1 = sum(c.value for c in curvatures) / len(curvatures)
        curved = sum(1 for c in curvatures if not c.is_flat)
        return CharacteristicClass(
            c1=c1, num_faces=len(curvatures), num_curved=curved
        )

    # -- stratification ----------------------------------------------------

    def stratification(self) -> dict[str, TrustStratum]:
        """Return a copy of the trust stratification."""
        return dict(self._strata)

    # -- full diagnostic ---------------------------------------------------

    def diagnose(self) -> dict[str, Any]:
        """Full diagnostic of the judgment bundle.

        Returns a dictionary containing:

        * ``coordinates`` — sorted list of coordinate keys.
        * ``total_judgments`` — total judgment count.
        * ``fiber_stats`` — per-fiber statistics.
        * ``first_chern_class`` — Chern class summary.
        * ``curvatures`` — list of non-flat curvature records.
        * ``holonomy`` — holonomy around the full coordinate loop
          (if ≥ 3 coordinates).
        * ``stratification`` — judgment counts per trust level.
        * ``stratum_contradictions`` — trust levels with internal
          contradictions.
        * ``bundle_is_flat`` — ``True`` iff *c₁ ≈ 0* and holonomy
          is trivial.

        Returns
        -------
        dict[str, Any]
            The diagnostic dictionary.
        """
        if not self._connection_built:
            self.build_connection()

        keys = sorted(self._fibers.keys())
        chern = self.first_chern_class()

        # All curvatures
        curvatures: list[BundleCurvature] = []
        for triple in combinations(keys, 3):
            curvatures.append(self.curvature(*triple))
        non_flat = [c for c in curvatures if not c.is_flat]

        # Holonomy
        hol: BundleHolonomy | None = None
        if len(keys) >= 3:
            hol = self.holonomy(keys)

        # Stratification summary
        strata_info: dict[str, int] = {}
        strata_contradictions: list[str] = []
        for name, stratum in self._strata.items():
            strata_info[name] = len(stratum.judgments)
            if stratum.has_internal_contradiction:
                strata_contradictions.append(name)

        # Fiber statistics
        fiber_stats: dict[str, dict[str, Any]] = {
            k: {
                "judgments": len(f.judgments),
                "avg_trust": f.average_trust,
                "channels": [c.value for c in f.channels],
                "has_contradiction": f.has_contradiction(),
            }
            for k, f in self._fibers.items()
        }

        return {
            "coordinates": keys,
            "total_judgments": sum(len(f) for f in self._fibers.values()),
            "fiber_stats": fiber_stats,
            "first_chern_class": {
                "c1": chern.c1,
                "interpretation": chern.interpretation,
                "num_faces": chern.num_faces,
                "num_curved": chern.num_curved,
            },
            "curvatures": [
                {
                    "vertices": c.vertices,
                    "value": c.value,
                    "flat": c.is_flat,
                    "interpretation": c.interpretation,
                }
                for c in non_flat
            ],
            "holonomy": (
                {
                    "loop": hol.loop,
                    "total_shift": hol.total_shift,
                    "trivial": hol.is_trivial,
                }
                if hol
                else None
            ),
            "stratification": strata_info,
            "stratum_contradictions": strata_contradictions,
            "bundle_is_flat": chern.is_flat
            and (hol is None or hol.is_trivial),
        }

    # -- human-readable summary --------------------------------------------

    def summary_text(self) -> str:
        """Human-readable summary of the bundle state.

        Produces a multi-line diagnostic string suitable for terminal
        output or copilot display panels.

        Returns
        -------
        str
            The formatted summary.
        """
        d = self.diagnose()
        lines = [
            "═══ Verification Bundle Diagnostic ═══",
            f"  Coordinates: {len(d['coordinates'])}",
            f"  Total judgments: {d['total_judgments']}",
            "",
            "  Connection Geometry:",
            f"    First Chern class c₁ = {d['first_chern_class']['c1']:+.4f}",
            f"    {d['first_chern_class']['interpretation']}",
            f"    Curved faces: {d['first_chern_class']['num_curved']}"
            f" / {d['first_chern_class']['num_faces']}",
            f"    Bundle is flat: "
            f"{'Yes ✓' if d.get('bundle_is_flat') else 'No ✗'}",
        ]
        if d["holonomy"]:
            lines.extend(
                [
                    "",
                    "  Holonomy:",
                    f"    Total shift: {d['holonomy']['total_shift']:.2f}",
                    f"    Trivial: "
                    f"{'Yes ✓' if d['holonomy']['trivial'] else 'NO — topological defect'}",
                ]
            )
        if d["curvatures"]:
            lines.extend(["", "  Non-flat curvatures:"])
            for c in d["curvatures"][:5]:
                lines.append(
                    f"    {c['vertices']}: {c['value']:+.4f}"
                    f" ({c['interpretation']})"
                )
            if len(d["curvatures"]) > 5:
                lines.append(
                    f"    ... and {len(d['curvatures']) - 5} more"
                )
        if d["stratification"]:
            lines.extend(["", "  Trust Stratification:"])
            for name, count in sorted(d["stratification"].items()):
                lines.append(f"    {name}: {count} judgments")
        if d["stratum_contradictions"]:
            lines.extend(["", "  ⚠ Intra-stratum contradictions:"])
            for s in d["stratum_contradictions"]:
                lines.append(f"    {s}")
        return "\n".join(lines)

    # -- reset -------------------------------------------------------------

    def reset(self) -> None:
        """Clear all fibers, the connection, and the stratification."""
        self._fibers.clear()
        self._connection = TrustConnection()
        self._strata.clear()
        self._connection_built = False
