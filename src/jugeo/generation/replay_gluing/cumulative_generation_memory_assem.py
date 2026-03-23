"""
Cumulative generation memory: assembling memory from all past generation episodes.

This module implements the machinery for accumulating, compressing, and querying
memory assembled from all past generation episodes in the jugeo system.

Mathematical background
-----------------------
Judgments are represented as 8-tuples:
    (c, φ, A, E, O, B, T, Π)
where:
    c  = context (str identifier)
    φ  = formula / proposition being judged
    A  = assumptions (tuple of strings)
    E  = evidence (tuple of strings)
    O  = obstructions – Čech H¹ cohomology classes (tuple of complex numbers)
    B  = blame assignment (str)
    T  = trust tier (TrustTier enum value)
    Π  = proof obligations (tuple of strings)

Čech H¹ cohomology
------------------
Given an open cover U = {U_α} of a topological space X, a 1-cochain assigns a
transition function g_{αβ} ∈ ℂ* to each pair (U_α, U_β).  The coboundary
condition (cocycle condition) is g_{αβ} · g_{βγ} = g_{αγ}.  When this fails,
the discrepancy lives in H¹(U, ℂ*) and constitutes an obstruction to gluing.

Trust ordering
--------------
PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED

# copilot:
"""
from __future__ import annotations

import hashlib
import math
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Optional jugeo imports (wrapped per project convention)
# ---------------------------------------------------------------------------
try:
    from jugeo.core.context import BaseContext  # type: ignore
except ImportError:
    BaseContext = object  # type: ignore

try:
    from jugeo.core.formula import Formula  # type: ignore
except ImportError:
    Formula = object  # type: ignore

try:
    from jugeo.core.trust import TrustBase  # type: ignore
except ImportError:
    TrustBase = object  # type: ignore

try:
    from jugeo.generation.base import GenerationBase  # type: ignore
except ImportError:
    GenerationBase = object  # type: ignore

# ===========================================================================
# TrustTier – ordered enumeration for the trust algebra
# ===========================================================================


class TrustTier(IntEnum):
    """Ordered enumeration of trust levels in the jugeo proof system.

    The ordering is strict:
        PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED

    Each level represents a progressively stronger epistemic commitment:
    - PROPOSAL      : An unvalidated conjecture; may contain errors.
    - REVIEWED      : Peer-checked but not formally verified.
    - VERIFIED      : Passes automated checkers (type-checkers, linters, etc.).
    - RUNTIME_WITNESSED : Empirically confirmed during a live execution trace.
    - PROOF_BACKED  : Accompanied by a machine-checked formal proof.
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    # ------------------------------------------------------------------
    # Convenience helpers so TrustTier values can be compared directly
    # ------------------------------------------------------------------

    def __le__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        """Return True if self ≤ other in the trust ordering."""
        if not isinstance(other, TrustTier):
            return NotImplemented
        return self.value <= other.value

    def __lt__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        """Return True if self < other (strictly lower trust)."""
        if not isinstance(other, TrustTier):
            return NotImplemented
        return self.value < other.value

    def __ge__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        if not isinstance(other, TrustTier):
            return NotImplemented
        return self.value >= other.value

    def __gt__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        if not isinstance(other, TrustTier):
            return NotImplemented
        return self.value > other.value

    def meet(self, other: "TrustTier") -> "TrustTier":
        """Greatest lower bound (meet, ⊓)."""
        return TrustTier(min(self.value, other.value))

    def join(self, other: "TrustTier") -> "TrustTier":
        """Least upper bound (join, ⊔)."""
        return TrustTier(max(self.value, other.value))

    def promote(self) -> "TrustTier":
        """↑_π — promote one tier upward, clamped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> "TrustTier":
        """↓_χ — demote one tier downward, clamped at PROPOSAL."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))


# ===========================================================================
# TrustAlgebra – meet/join lattice over TrustTier
# ===========================================================================


class TrustAlgebra:
    """Companion class implementing the lattice operations on TrustTier values.

    The five trust tiers form a *total* order which is also a distributive
    lattice.  Under this structure:
        meet(a, b) = min(a, b)   (greatest lower bound)
        join(a, b) = max(a, b)   (least upper bound)

    This is used when combining evidence from multiple sources: the trust of
    a derived judgment can be no higher than the least-trusted premise
    (pessimistic meet), or we may escalate to the best available evidence
    (optimistic join).
    """

    @staticmethod
    def meet(a: TrustTier, b: TrustTier) -> TrustTier:
        """Return the greatest lower bound (minimum) of two trust tiers.

        In epistemic terms: a derived claim is only as trustworthy as its
        weakest supporting premise.

        Parameters
        ----------
        a, b : TrustTier
            The two trust levels to combine.

        Returns
        -------
        TrustTier
            The lower of the two trust tiers.
        """
        return a if a.value <= b.value else b

    @staticmethod
    def join(a: TrustTier, b: TrustTier) -> TrustTier:
        """Return the least upper bound (maximum) of two trust tiers.

        Use when the *best* available piece of evidence should determine the
        trust level of a composite memory entry.

        Parameters
        ----------
        a, b : TrustTier
            The two trust levels to compare.

        Returns
        -------
        TrustTier
            The higher of the two trust tiers.
        """
        return a if a.value >= b.value else b

    @staticmethod
    def __le__(a: TrustTier, b: TrustTier) -> bool:
        """Delegate to TrustTier ordering."""
        return a <= b

    @staticmethod
    def __lt__(a: TrustTier, b: TrustTier) -> bool:
        """Delegate to TrustTier strict ordering."""
        return a < b

    @staticmethod
    def below_threshold(tier: TrustTier, threshold: TrustTier) -> bool:
        """Return True when *tier* is strictly below *threshold*.

        Useful for filtering episodes that do not meet a minimum trust bar.

        Parameters
        ----------
        tier : TrustTier
            The trust level to test.
        threshold : TrustTier
            The minimum acceptable trust level.

        Returns
        -------
        bool
        """
        return tier < threshold

    @staticmethod
    def tier_distance(a: TrustTier, b: TrustTier) -> int:
        """Return the integer distance between two tiers in the lattice.

        Parameters
        ----------
        a, b : TrustTier

        Returns
        -------
        int
            Absolute difference of numeric values.
        """
        return abs(a.value - b.value)


# ===========================================================================
# Čech cohomology helper
# ===========================================================================


class CechCohomology:
    """Simulate Čech H¹ cohomology for transition-function obstructions.

    Given an open cover U = {U_0, U_1, ..., U_n} of a base space X and a
    collection of transition functions g_{ij} ∈ ℂ* (non-zero complex numbers)
    assigned to each overlap U_i ∩ U_j, this class:

    1.  Checks whether the 1-cocycle condition holds:
            g_{ij} · g_{jk} = g_{ik}  for all i < j < k.

    2.  Computes the "obstruction class" – a tuple of complex residuals that
        measure how far the cover fails to satisfy the cocycle condition.

    3.  Determines whether the obstruction is trivial (all residuals = 1+0j).

    The mathematics is a finite-dimensional approximation to the full sheaf-
    theoretic construction; in practice the cover elements are strings (labels)
    and the transition functions are complex numbers representing phase and
    magnitude mismatches.

    Attributes
    ----------
    cover : dict[tuple[str, str], complex]
        Maps ordered pairs (U_i, U_j) to the transition g_{ij}.
    elements : list[str]
        Sorted list of unique cover element labels.
    """

    def __init__(self, cover: Dict[Tuple[str, str], complex]) -> None:
        """Initialise with a cover dictionary.

        Parameters
        ----------
        cover : dict
            Mapping (U_i, U_j) → g_{ij} ∈ ℂ.
            The convention is |i| < |j| lexicographically; missing pairs are
            treated as having transition value 1+0j (trivial).
        """
        self.cover: Dict[Tuple[str, str], complex] = dict(cover)
        # Collect unique element names
        elements_set: set = set()
        for (a, b) in cover:
            elements_set.add(a)
            elements_set.add(b)
        self.elements: List[str] = sorted(elements_set)

    def _g(self, i: str, j: str) -> complex:
        """Return transition g_{ij}, defaulting to 1 if not set."""
        if (i, j) in self.cover:
            return self.cover[(i, j)]
        if (j, i) in self.cover:
            # Anti-symmetry: g_{ji} = 1 / g_{ij}
            val = self.cover[(j, i)]
            return (1 / val) if val != 0 else 1.0
        return complex(1, 0)

    def coboundary_map(self) -> Dict[Tuple[str, str, str], complex]:
        """Compute the coboundary δ of the 1-cochain.

        For each ordered triple (i, j, k) of cover elements, the coboundary is:
            (δg)_{ijk} = g_{ij} · g_{jk} · (g_{ik})^{-1}

        A pure 1-cocycle satisfies (δg)_{ijk} = 1 for all triples.

        Returns
        -------
        dict
            Mapping (U_i, U_j, U_k) → (δg)_{ijk}.
        """
        result: Dict[Tuple[str, str, str], complex] = {}
        elems = self.elements
        for i in elems:
            for j in elems:
                if j <= i:
                    continue
                for k in elems:
                    if k <= j:
                        continue
                    gij = self._g(i, j)
                    gjk = self._g(j, k)
                    gik = self._g(i, k)
                    denom = gik if abs(gik) > 1e-15 else complex(1e-15, 0)
                    result[(i, j, k)] = gij * gjk / denom
        return result

    def compute_h1(self) -> Tuple[complex, ...]:
        """Return the obstruction class as a tuple of coboundary residuals.

        Each entry is the deviation from 1 of the coboundary on a triple.
        A trivial H¹ (all entries = 1+0j) means the line bundle glues.

        Returns
        -------
        tuple[complex, ...]
            One complex number per triple (i, j, k) with i < j < k.
        """
        cb = self.coboundary_map()
        # The obstruction class is represented as deviations from 1
        return tuple(v for v in cb.values())

    def is_trivial(self) -> bool:
        """Return True when every coboundary is 1 (no obstruction).

        In geometric terms: the transition functions satisfy the cocycle
        condition and the associated line bundle is trivial.

        Returns
        -------
        bool
        """
        h1 = self.compute_h1()
        tol = 1e-9
        return all(abs(v - 1.0) < tol for v in h1)

    def obstruction_class(self) -> Tuple[complex, ...]:
        """Return a canonical representative of the obstruction class.

        When the obstruction is trivial the tuple is empty.  Otherwise the
        non-unit coboundary values are returned as the representative.

        Returns
        -------
        tuple[complex, ...]
        """
        h1 = self.compute_h1()
        if self.is_trivial():
            return ()
        tol = 1e-9
        return tuple(v for v in h1 if abs(v - 1.0) >= tol)


# ===========================================================================
# make_judgment helper
# ===========================================================================


def make_judgment(
    context: str,
    formula: str,
    assumptions: Tuple[str, ...],
    evidence: Tuple[str, ...],
    obstructions: Tuple[complex, ...],
    blame: str,
    trust_tier: TrustTier,
    proof_obligations: Tuple[str, ...],
) -> Tuple:
    """Construct the canonical 8-tuple representation of a judgment.

    A judgment in the jugeo system is the primary epistemic unit.  It encodes
    not only the proposition being asserted but also the full provenance chain
    (assumptions, evidence, blame) and the category-theoretic obstruction that
    may prevent the judgment from being lifted to a global section.

    Parameters
    ----------
    context : str
        Identifier for the local context Γ in which the judgment is made.
    formula : str
        The proposition φ being judged (in serialised form).
    assumptions : tuple[str, ...]
        Named assumptions A that the judgment depends on.
    evidence : tuple[str, ...]
        Evidence items E supporting the judgment.
    obstructions : tuple[complex, ...]
        Čech H¹ cohomology obstruction class O.
    blame : str
        The agent or module B responsible for the judgment.
    trust_tier : TrustTier
        The trust level T assigned to this judgment.
    proof_obligations : tuple[str, ...]
        Remaining proof obligations Π that must be discharged.

    Returns
    -------
    tuple
        The 8-tuple (c, φ, A, E, O, B, T, Π).
    """
    return (
        context,
        formula,
        assumptions,
        evidence,
        obstructions,
        blame,
        trust_tier,
        proof_obligations,
    )


# ===========================================================================
# Primary frozen dataclasses
# ===========================================================================


@dataclass(frozen=True)
class GenerationEpisode:
    """An immutable record of a single generation episode.

    A generation episode is one atomic execution of the inhabitant-generation
    process over a specific context.  The episode records which cover elements
    were visited, which inhabitants were produced, the elapsed time, and the
    Čech obstruction class that arose during gluing.

    Attributes
    ----------
    episode_id : str
        Globally unique identifier for this episode (UUID recommended).
    generation_context : str
        Serialised description of the context Γ in which generation ran.
    inhabitants_generated : tuple[str, ...]
        Identifiers of the inhabitants (proof terms / witnesses) produced.
    cover_elements_visited : tuple[str, ...]
        Labels of the open cover elements U_α traversed during generation.
    duration_ms : float
        Wall-clock duration of the episode in milliseconds.
    trust_tier : TrustTier
        Trust level assigned to the outputs of this episode.
    cech_class : tuple[complex, ...]
        Čech H¹ obstruction class computed over the visited cover.
    judgment : tuple
        The 8-tuple judgment attached to this episode.
    """

    episode_id: str
    generation_context: str
    inhabitants_generated: Tuple[str, ...]
    cover_elements_visited: Tuple[str, ...]
    duration_ms: float
    trust_tier: TrustTier
    cech_class: Tuple[complex, ...]
    judgment: tuple  # 8-tuple (c, φ, A, E, O, B, T, Π)


@dataclass(frozen=True)
class CumulativeGenerationMemory:
    """Accumulated memory assembled from multiple generation episodes.

    This is the central data structure of the module.  It aggregates episodes
    into a compressed representation that can be queried efficiently.  The
    Čech obstruction field records whether the combined memory is globally
    consistent (trivial obstruction) or contains patches that cannot be glued.

    Attributes
    ----------
    memory_id : str
        Unique identifier for this memory snapshot.
    episodes : tuple[str, ...]
        Identifiers of the constituent episodes (in assembly order).
    compressed_representation : tuple[str, ...]
        Compressed tokens / summaries of the episode content.
    total_generations : int
        Total number of inhabitants generated across all episodes.
    trust_tier : TrustTier
        Aggregate trust level (typically the meet of episode trust tiers).
    cech_obstruction : tuple[complex, ...]
        Combined Čech H¹ obstruction class.
    judgment : tuple
        8-tuple judgment for the assembled memory.
    """

    memory_id: str
    episodes: Tuple[str, ...]
    compressed_representation: Tuple[str, ...]
    total_generations: int
    trust_tier: TrustTier
    cech_obstruction: Tuple[complex, ...]
    judgment: tuple  # 8-tuple


@dataclass(frozen=True)
class MemoryAssembly:
    """Metadata describing how a CumulativeGenerationMemory was assembled.

    Records the provenance of a memory object: which episodes were combined,
    which strategy was used, and what quality score the assembly achieved.

    Attributes
    ----------
    assembly_id : str
        Unique identifier for this assembly operation.
    source_episodes : tuple[str, ...]
        Identifiers of the episodes fed into the assembly.
    assembly_strategy : str
        Name of the assembly strategy (e.g. 'sequential', 'hierarchical').
    resulting_memory_id : str
        Identifier of the CumulativeGenerationMemory that resulted.
    quality_score : float
        A [0, 1] quality estimate of the assembled memory.
    trust_tier : TrustTier
        Trust level of the assembly operation itself.
    """

    assembly_id: str
    source_episodes: Tuple[str, ...]
    assembly_strategy: str
    resulting_memory_id: str
    quality_score: float
    trust_tier: TrustTier


@dataclass(frozen=True)
class MemoryCatalog:
    """A catalogue mapping episode identifiers to memory content hashes.

    The catalogue provides a versioned index for efficient lookup and
    deduplication.  Each entry is a (episode_id, memory_hash) pair.

    Attributes
    ----------
    catalog_id : str
        Unique identifier for this catalog snapshot.
    entries : tuple[tuple[str, str], ...]
        Pairs of (episode_id, sha256_hash_of_content).
    index_version : int
        Monotone version counter; incremented on every rebuild.
    trust_tier : TrustTier
        Trust level of the catalog metadata.
    obstruction_catalog : tuple[tuple[complex, ...], ...]
        Per-episode obstruction classes in the same order as entries.
    """

    catalog_id: str
    entries: Tuple[Tuple[str, str], ...]
    index_version: int
    trust_tier: TrustTier
    obstruction_catalog: Tuple[Tuple[complex, ...], ...]


# ===========================================================================
# Module-level example episodes (EPISODE_1 … EPISODE_5)
# ===========================================================================

_J1 = make_judgment(
    context="ctx-alpha",
    formula="∀x. P(x) → Q(x)",
    assumptions=("hyp-P",),
    evidence=("witness-alpha-1",),
    obstructions=(complex(1, 0),),
    blame="generator-alpha",
    trust_tier=TrustTier.PROPOSAL,
    proof_obligations=("discharge-P-premise",),
)

EPISODE_1 = GenerationEpisode(
    episode_id="ep-0001",
    generation_context="ctx-alpha",
    inhabitants_generated=("inh-alpha-1", "inh-alpha-2"),
    cover_elements_visited=("U0", "U1"),
    duration_ms=42.7,
    trust_tier=TrustTier.PROPOSAL,
    cech_class=(complex(1, 0), complex(0.95, 0.1)),
    judgment=_J1,
)

_J2 = make_judgment(
    context="ctx-beta",
    formula="∃x. R(x)",
    assumptions=("hyp-R",),
    evidence=("witness-beta-1", "witness-beta-2"),
    obstructions=(),
    blame="generator-beta",
    trust_tier=TrustTier.REVIEWED,
    proof_obligations=(),
)

EPISODE_2 = GenerationEpisode(
    episode_id="ep-0002",
    generation_context="ctx-beta",
    inhabitants_generated=("inh-beta-1",),
    cover_elements_visited=("U1", "U2"),
    duration_ms=88.3,
    trust_tier=TrustTier.REVIEWED,
    cech_class=(complex(1, 0),),
    judgment=_J2,
)

_J3 = make_judgment(
    context="ctx-gamma",
    formula="P(a) ∧ Q(b)",
    assumptions=("hyp-conj",),
    evidence=("witness-gamma-1",),
    obstructions=(complex(0.9, -0.1),),
    blame="generator-gamma",
    trust_tier=TrustTier.VERIFIED,
    proof_obligations=("verify-conjunction",),
)

EPISODE_3 = GenerationEpisode(
    episode_id="ep-0003",
    generation_context="ctx-gamma",
    inhabitants_generated=("inh-gamma-1", "inh-gamma-2", "inh-gamma-3"),
    cover_elements_visited=("U0", "U2", "U3"),
    duration_ms=130.0,
    trust_tier=TrustTier.VERIFIED,
    cech_class=(complex(0.9, -0.1), complex(1, 0)),
    judgment=_J3,
)

_J4 = make_judgment(
    context="ctx-delta",
    formula="¬P(c)",
    assumptions=(),
    evidence=("runtime-trace-42",),
    obstructions=(),
    blame="runtime-monitor",
    trust_tier=TrustTier.RUNTIME_WITNESSED,
    proof_obligations=(),
)

EPISODE_4 = GenerationEpisode(
    episode_id="ep-0004",
    generation_context="ctx-delta",
    inhabitants_generated=("inh-delta-1",),
    cover_elements_visited=("U3",),
    duration_ms=5.1,
    trust_tier=TrustTier.RUNTIME_WITNESSED,
    cech_class=(complex(1, 0),),
    judgment=_J4,
)

_J5 = make_judgment(
    context="ctx-epsilon",
    formula="∀x∀y. S(x, y) → S(y, x)",
    assumptions=("hyp-sym",),
    evidence=("formal-proof-sym-001",),
    obstructions=(),
    blame="proof-checker",
    trust_tier=TrustTier.PROOF_BACKED,
    proof_obligations=(),
)

EPISODE_5 = GenerationEpisode(
    episode_id="ep-0005",
    generation_context="ctx-epsilon",
    inhabitants_generated=("inh-epsilon-1", "inh-epsilon-2"),
    cover_elements_visited=("U0", "U1", "U2", "U3"),
    duration_ms=201.9,
    trust_tier=TrustTier.PROOF_BACKED,
    cech_class=(complex(1, 0), complex(1, 0), complex(1, 0)),
    judgment=_J5,
)

_ALL_EPISODES: Tuple[GenerationEpisode, ...] = (
    EPISODE_1,
    EPISODE_2,
    EPISODE_3,
    EPISODE_4,
    EPISODE_5,
)


# ===========================================================================
# EpisodeStore
# ===========================================================================


class EpisodeStore:
    """Mutable, indexed store for GenerationEpisode objects.

    The store maintains three internal indices:
    - by_id            : episode_id → GenerationEpisode
    - by_cover_element : cover_element_label → list[episode_id]
    - by_type          : trust_tier → list[episode_id]

    These allow O(1) retrieval by ID and O(k) retrieval by cover element or
    trust tier where k is the number of matching episodes.

    Attributes
    ----------
    _episodes : dict[str, GenerationEpisode]
        Primary index keyed by episode_id.
    _cover_index : dict[str, list[str]]
        Secondary index mapping cover element labels to episode IDs.
    _type_index : dict[TrustTier, list[str]]
        Secondary index mapping trust tiers to episode IDs.
    """

    def __init__(self) -> None:
        """Initialise an empty episode store with all indices."""
        self._episodes: Dict[str, GenerationEpisode] = {}
        self._cover_index: Dict[str, List[str]] = defaultdict(list)
        self._type_index: Dict[TrustTier, List[str]] = defaultdict(list)
        self._insertion_order: List[str] = []

    def add_episode(self, episode: GenerationEpisode) -> None:
        """Add an episode to the store and update all indices.

        If an episode with the same ID already exists it is silently replaced
        and the indices are updated accordingly.

        Parameters
        ----------
        episode : GenerationEpisode
            The episode to add.
        """
        eid = episode.episode_id
        # If replacing, remove old index entries first
        if eid in self._episodes:
            self._remove_from_indices(self._episodes[eid])
        else:
            self._insertion_order.append(eid)
        self._episodes[eid] = episode
        # Update cover element index
        for elem in episode.cover_elements_visited:
            if eid not in self._cover_index[elem]:
                self._cover_index[elem].append(eid)
        # Update trust tier index
        if eid not in self._type_index[episode.trust_tier]:
            self._type_index[episode.trust_tier].append(eid)

    def _remove_from_indices(self, episode: GenerationEpisode) -> None:
        """Internal helper: remove index entries for an episode."""
        eid = episode.episode_id
        for elem in episode.cover_elements_visited:
            if eid in self._cover_index[elem]:
                self._cover_index[elem].remove(eid)
        if eid in self._type_index[episode.trust_tier]:
            self._type_index[episode.trust_tier].remove(eid)

    def get_by_id(self, episode_id: str) -> Optional[GenerationEpisode]:
        """Retrieve an episode by its unique identifier.

        Parameters
        ----------
        episode_id : str

        Returns
        -------
        GenerationEpisode or None
        """
        return self._episodes.get(episode_id)

    def get_by_cover_element(self, element: str) -> Tuple[GenerationEpisode, ...]:
        """Return all episodes that visited a given cover element.

        Parameters
        ----------
        element : str
            Label of the cover element (e.g. 'U0', 'U3').

        Returns
        -------
        tuple[GenerationEpisode, ...]
        """
        ids = self._cover_index.get(element, [])
        return tuple(self._episodes[eid] for eid in ids if eid in self._episodes)

    def get_by_type(self, trust_tier: TrustTier) -> Tuple[GenerationEpisode, ...]:
        """Return all episodes with a specific trust tier.

        Parameters
        ----------
        trust_tier : TrustTier

        Returns
        -------
        tuple[GenerationEpisode, ...]
        """
        ids = self._type_index.get(trust_tier, [])
        return tuple(self._episodes[eid] for eid in ids if eid in self._episodes)

    def remove_episode(self, episode_id: str) -> bool:
        """Remove an episode and clean up all index entries.

        Parameters
        ----------
        episode_id : str

        Returns
        -------
        bool
            True if the episode existed and was removed; False otherwise.
        """
        if episode_id not in self._episodes:
            return False
        ep = self._episodes.pop(episode_id)
        self._remove_from_indices(ep)
        if episode_id in self._insertion_order:
            self._insertion_order.remove(episode_id)
        return True

    def list_all(self) -> Tuple[GenerationEpisode, ...]:
        """Return all stored episodes in insertion order.

        Returns
        -------
        tuple[GenerationEpisode, ...]
        """
        return tuple(
            self._episodes[eid]
            for eid in self._insertion_order
            if eid in self._episodes
        )

    def count(self) -> int:
        """Return the number of episodes currently stored.

        Returns
        -------
        int
        """
        return len(self._episodes)

    def get_statistics(self) -> Dict[str, Any]:
        """Compute summary statistics over all stored episodes.

        Returns
        -------
        dict
            Keys: total_episodes, total_inhabitants, mean_duration_ms,
                  trust_tier_distribution, cover_element_counts.
        """
        episodes = list(self._episodes.values())
        if not episodes:
            return {
                "total_episodes": 0,
                "total_inhabitants": 0,
                "mean_duration_ms": 0.0,
                "trust_tier_distribution": {},
                "cover_element_counts": {},
            }
        durations = [ep.duration_ms for ep in episodes]
        total_inh = sum(len(ep.inhabitants_generated) for ep in episodes)
        trust_dist: Dict[str, int] = defaultdict(int)
        for ep in episodes:
            trust_dist[ep.trust_tier.name] += 1
        cover_counts: Dict[str, int] = defaultdict(int)
        for ep in episodes:
            for elem in ep.cover_elements_visited:
                cover_counts[elem] += 1
        return {
            "total_episodes": len(episodes),
            "total_inhabitants": total_inh,
            "mean_duration_ms": statistics.mean(durations),
            "trust_tier_distribution": dict(trust_dist),
            "cover_element_counts": dict(cover_counts),
        }


# ===========================================================================
# MemoryCompressor
# ===========================================================================


class MemoryCompressor:
    """Compress and decompress episode content using run-length encoding
    combined with a simulated semantic deduplication pass.

    Compression pipeline
    --------------------
    1. Tokenise the episode identifier stream.
    2. Apply run-length encoding to consecutive identical tokens.
    3. Apply semantic deduplication: tokens that differ only by a numeric
       suffix are collapsed to a single canonical form with a count prefix.
    4. Return the compressed stream as a list of strings.

    The decompression pipeline reverses these steps.
    """

    def __init__(self, min_run_length: int = 2) -> None:
        """Initialise the compressor.

        Parameters
        ----------
        min_run_length : int
            Minimum run length to trigger RLE encoding (default 2).
        """
        self.min_run_length = min_run_length
        # Internal checksum registry: token → crc-like int
        self._checksum_registry: Dict[str, int] = {}

    def compress(self, tokens: Sequence[str]) -> List[str]:
        """Compress a token sequence using RLE + semantic dedup.

        Parameters
        ----------
        tokens : sequence of str

        Returns
        -------
        list[str]
            Compressed token list.
        """
        rle = self.run_length_encode(list(tokens))
        deduped = self.semantic_dedup(rle)
        return deduped

    def decompress(self, compressed: Sequence[str]) -> List[str]:
        """Decompress a previously compressed token sequence.

        Parameters
        ----------
        compressed : sequence of str

        Returns
        -------
        list[str]
        """
        # Reverse semantic dedup (expand COUNT:ROOT tokens)
        expanded: List[str] = []
        for tok in compressed:
            if ":" in tok:
                parts = tok.split(":", 1)
                try:
                    count = int(parts[0])
                    for i in range(count):
                        expanded.append(f"{parts[1]}-{i}")
                except ValueError:
                    expanded.append(tok)
            else:
                expanded.append(tok)
        # Reverse RLE
        return self.run_length_decode(expanded)

    def run_length_encode(self, tokens: List[str]) -> List[str]:
        """Apply run-length encoding to a token list.

        Consecutive runs of the same token 't' of length n ≥ min_run_length
        are encoded as 'n×t'.

        Parameters
        ----------
        tokens : list[str]

        Returns
        -------
        list[str]
        """
        if not tokens:
            return []
        result: List[str] = []
        run_tok = tokens[0]
        run_count = 1
        for tok in tokens[1:]:
            if tok == run_tok:
                run_count += 1
            else:
                if run_count >= self.min_run_length:
                    result.append(f"{run_count}x{run_tok}")
                else:
                    result.extend([run_tok] * run_count)
                run_tok = tok
                run_count = 1
        # Flush last run
        if run_count >= self.min_run_length:
            result.append(f"{run_count}x{run_tok}")
        else:
            result.extend([run_tok] * run_count)
        return result

    def run_length_decode(self, tokens: List[str]) -> List[str]:
        """Decode a run-length-encoded token list.

        Tokens of the form 'NxT' are expanded to N copies of 'T'.

        Parameters
        ----------
        tokens : list[str]

        Returns
        -------
        list[str]
        """
        result: List[str] = []
        for tok in tokens:
            if "x" in tok:
                idx = tok.index("x")
                prefix = tok[:idx]
                suffix = tok[idx + 1 :]
                try:
                    count = int(prefix)
                    result.extend([suffix] * count)
                    continue
                except ValueError:
                    pass
            result.append(tok)
        return result

    def semantic_dedup(self, tokens: List[str]) -> List[str]:
        """Collapse tokens sharing a common root (strip trailing digits).

        Tokens that share the same alphabetic root are merged to 'N:ROOT'
        where N is the count of distinct variants seen.

        Parameters
        ----------
        tokens : list[str]

        Returns
        -------
        list[str]
        """
        from itertools import groupby

        def root(tok: str) -> str:
            """Strip trailing digits and hyphens from a token."""
            return tok.rstrip("0123456789-").rstrip("-")

        result: List[str] = []
        for r, group in groupby(tokens, key=root):
            items = list(group)
            if len(items) > 1:
                result.append(f"{len(items)}:{r}")
            else:
                result.append(items[0])
        return result

    def estimate_compression_ratio(self, original: Sequence[str]) -> float:
        """Estimate the compression ratio for a token sequence.

        Returns len(compressed) / len(original).  A value < 1 means
        compression was beneficial.

        Parameters
        ----------
        original : sequence[str]

        Returns
        -------
        float
        """
        original_list = list(original)
        if not original_list:
            return 1.0
        compressed = self.compress(original_list)
        return len(compressed) / len(original_list)

    def compute_checksum(self, tokens: Sequence[str]) -> str:
        """Compute a SHA-256 checksum of a token sequence.

        The checksum is used to detect corruption after compression/
        decompression cycles.

        Parameters
        ----------
        tokens : sequence[str]

        Returns
        -------
        str
            Hex digest string.
        """
        payload = "\x00".join(tokens).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


# ===========================================================================
# EpisodeRetriever (BM25-style scoring)
# ===========================================================================


class EpisodeRetriever:
    """Retrieve relevant episodes using a BM25-style ranking function.

    BM25 (Best Match 25) is a probabilistic retrieval model.  Given a query
    token set Q and a document d, the score is:

        BM25(Q, d) = Σ_{t ∈ Q}  IDF(t) · (tf(t,d) · (k1+1))
                                            / (tf(t,d) + k1·(1 - b + b·|d|/avgdl))

    where:
        tf(t, d)  = term frequency of t in document d
        IDF(t)    = log((N - n_t + 0.5) / (n_t + 0.5) + 1)
        N         = total number of documents
        n_t       = number of documents containing term t
        |d|       = document length (tokens)
        avgdl     = average document length
        k1, b     = free parameters (defaults 1.5 and 0.75)

    Attributes
    ----------
    k1 : float
        Term saturation parameter.
    b : float
        Length normalisation parameter.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        """Initialise the retriever with BM25 hyper-parameters.

        Parameters
        ----------
        k1 : float
            Controls term frequency saturation.
        b : float
            Controls document-length normalisation.
        """
        self.k1 = k1
        self.b = b
        self._episodes: Dict[str, GenerationEpisode] = {}
        self._tf: Dict[str, Dict[str, float]] = {}   # episode_id → token → tf
        self._df: Dict[str, int] = defaultdict(int)   # token → doc frequency
        self._doc_lengths: Dict[str, int] = {}

    def index_episode(self, episode: GenerationEpisode) -> None:
        """Add an episode to the retrieval index.

        The indexable text is constructed from the episode's context,
        inhabitant identifiers, and cover element labels.

        Parameters
        ----------
        episode : GenerationEpisode
        """
        eid = episode.episode_id
        self._episodes[eid] = episode
        tokens = self.tokenize(
            episode.generation_context
            + " "
            + " ".join(episode.inhabitants_generated)
            + " "
            + " ".join(episode.cover_elements_visited)
        )
        self._doc_lengths[eid] = len(tokens)
        tf_map: Dict[str, float] = defaultdict(float)
        for tok in tokens:
            tf_map[tok] += 1.0
        self._tf[eid] = dict(tf_map)
        # Update document frequency
        for tok in set(tokens):
            self._df[tok] += 1

    def tokenize(self, text: str) -> List[str]:
        """Split text into lowercase alphabetic tokens, removing stopwords.

        Parameters
        ----------
        text : str

        Returns
        -------
        list[str]
        """
        stopwords = {"a", "an", "the", "and", "or", "in", "of", "to", "for"}
        raw = text.lower().replace("-", " ").replace("_", " ").split()
        return [w for w in raw if w.isalpha() and w not in stopwords]

    def compute_idf(self, term: str) -> float:
        """Compute the IDF weight for a single term.

        Parameters
        ----------
        term : str

        Returns
        -------
        float
        """
        n_docs = len(self._episodes)
        if n_docs == 0:
            return 0.0
        n_t = self._df.get(term, 0)
        return math.log((n_docs - n_t + 0.5) / (n_t + 0.5) + 1)

    def compute_tf(self, term: str, episode_id: str) -> float:
        """Compute raw term frequency for a term in a specific episode.

        Parameters
        ----------
        term : str
        episode_id : str

        Returns
        -------
        float
        """
        return self._tf.get(episode_id, {}).get(term, 0.0)

    def bm25_score(self, query_tokens: List[str], episode_id: str) -> float:
        """Compute the BM25 score for a query against a single episode.

        Parameters
        ----------
        query_tokens : list[str]
            Tokenised query terms.
        episode_id : str

        Returns
        -------
        float
        """
        if not self._doc_lengths or not self._episodes:
            return 0.0
        avgdl = sum(self._doc_lengths.values()) / len(self._doc_lengths)
        dl = self._doc_lengths.get(episode_id, 0)
        score = 0.0
        for term in query_tokens:
            idf = self.compute_idf(term)
            tf = self.compute_tf(term, episode_id)
            denom = tf + self.k1 * (1 - self.b + self.b * dl / max(avgdl, 1))
            score += idf * (tf * (self.k1 + 1)) / max(denom, 1e-15)
        return score

    def retrieve(self, query: str, top_k: int = 5) -> Tuple[GenerationEpisode, ...]:
        """Retrieve the top-k episodes most relevant to a query string.

        Parameters
        ----------
        query : str
            Free-text search query.
        top_k : int
            Maximum number of results to return.

        Returns
        -------
        tuple[GenerationEpisode, ...]
            Episodes ordered by descending BM25 score.
        """
        qtoks = self.tokenize(query)
        scored = [
            (self.bm25_score(qtoks, eid), eid)
            for eid in self._episodes
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return tuple(
            self._episodes[eid] for _, eid in scored[:top_k] if eid in self._episodes
        )

    def rank_episodes(self, query: str) -> List[Tuple[float, str]]:
        """Return a full ranked list of (score, episode_id) pairs.

        Parameters
        ----------
        query : str

        Returns
        -------
        list[tuple[float, str]]
        """
        qtoks = self.tokenize(query)
        scored = [
            (self.bm25_score(qtoks, eid), eid)
            for eid in self._episodes
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored


# ===========================================================================
# CumulativeIndex
# ===========================================================================


class CumulativeIndex:
    """A persistent-snapshot index mapping keys to sets of episode IDs.

    The index supports insertion, lookup, merge with another index, pruning
    of stale entries, and snapshotting / restoring for rollback.

    Internally the index is a dict[str, set[str]].  Snapshots are stored as
    a list of shallow copies.
    """

    def __init__(self) -> None:
        """Initialise an empty cumulative index."""
        self._data: Dict[str, set] = defaultdict(set)
        self._snapshots: List[Dict[str, set]] = []
        self._version: int = 0

    def insert(self, key: str, episode_id: str) -> None:
        """Associate an episode ID with a key.

        Parameters
        ----------
        key : str
            Index key (e.g. a cover element label, context name, etc.).
        episode_id : str
        """
        self._data[key].add(episode_id)
        self._version += 1

    def lookup(self, key: str) -> frozenset:
        """Return all episode IDs associated with a key.

        Parameters
        ----------
        key : str

        Returns
        -------
        frozenset[str]
        """
        return frozenset(self._data.get(key, set()))

    def merge(self, other: "CumulativeIndex") -> None:
        """Merge another index into this one (union of sets per key).

        Parameters
        ----------
        other : CumulativeIndex
        """
        for key, ids in other._data.items():
            self._data[key] |= ids
        self._version += 1

    def prune(self, obsolete_ids: Sequence[str]) -> int:
        """Remove a set of episode IDs from all keys.

        Parameters
        ----------
        obsolete_ids : sequence of str
            Episode IDs to remove everywhere in the index.

        Returns
        -------
        int
            Number of (key, id) pairs removed.
        """
        obsolete = set(obsolete_ids)
        removed = 0
        for key in list(self._data.keys()):
            before = len(self._data[key])
            self._data[key] -= obsolete
            removed += before - len(self._data[key])
            if not self._data[key]:
                del self._data[key]
        self._version += 1
        return removed

    def snapshot(self) -> int:
        """Save a shallow snapshot of the current index state.

        Returns
        -------
        int
            Snapshot ID (position in the snapshots list).
        """
        snap = {k: set(v) for k, v in self._data.items()}
        self._snapshots.append(snap)
        return len(self._snapshots) - 1

    def restore(self, snapshot_id: int) -> None:
        """Restore the index to a previously saved snapshot.

        Parameters
        ----------
        snapshot_id : int
            Index into the snapshots list as returned by snapshot().
        """
        if snapshot_id < 0 or snapshot_id >= len(self._snapshots):
            raise IndexError(f"No snapshot with id {snapshot_id}")
        self._data = defaultdict(set, {k: set(v) for k, v in self._snapshots[snapshot_id].items()})
        self._version += 1

    @property
    def version(self) -> int:
        """Current version counter (incremented on every mutation)."""
        return self._version

    def all_keys(self) -> Tuple[str, ...]:
        """Return all keys currently in the index."""
        return tuple(sorted(self._data.keys()))


# ===========================================================================
# MemoryConsolidator
# ===========================================================================


class MemoryConsolidator:
    """Detect and merge overlapping generation episodes.

    Two episodes are considered overlapping when they share at least one cover
    element *and* their generation contexts are compatible (same prefix up to
    the first '-').

    The overlap score is Jaccard similarity on the set of cover elements
    visited:
        J(A, B) = |A ∩ B| / |A ∪ B|

    A pair with J ≥ overlap_threshold is a candidate for merging.
    """

    def __init__(self, overlap_threshold: float = 0.3) -> None:
        """Initialise the consolidator.

        Parameters
        ----------
        overlap_threshold : float
            Minimum Jaccard overlap score to treat two episodes as overlapping.
        """
        self.overlap_threshold = overlap_threshold

    def compute_overlap_score(
        self, ep1: GenerationEpisode, ep2: GenerationEpisode
    ) -> float:
        """Compute the Jaccard similarity between the cover sets of two episodes.

        Parameters
        ----------
        ep1, ep2 : GenerationEpisode

        Returns
        -------
        float in [0, 1]
        """
        set1 = set(ep1.cover_elements_visited)
        set2 = set(ep2.cover_elements_visited)
        if not set1 and not set2:
            return 1.0
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return intersection / union if union > 0 else 0.0

    def detect_overlaps(
        self, episodes: Sequence[GenerationEpisode]
    ) -> List[Tuple[str, str, float]]:
        """Find all pairs of overlapping episodes.

        Parameters
        ----------
        episodes : sequence of GenerationEpisode

        Returns
        -------
        list[tuple[str, str, float]]
            Each item is (episode_id_1, episode_id_2, overlap_score).
        """
        pairs: List[Tuple[str, str, float]] = []
        ep_list = list(episodes)
        for i, ep1 in enumerate(ep_list):
            for ep2 in ep_list[i + 1:]:
                score = self.compute_overlap_score(ep1, ep2)
                if score >= self.overlap_threshold:
                    pairs.append((ep1.episode_id, ep2.episode_id, score))
        return pairs

    def merge_episodes(
        self, ep1: GenerationEpisode, ep2: GenerationEpisode
    ) -> GenerationEpisode:
        """Merge two overlapping episodes into a single composite episode.

        The merged episode receives:
        - A new episode_id composed from both source IDs.
        - The union of inhabitants generated.
        - The union of cover elements visited.
        - The sum of durations.
        - The meet of trust tiers (conservative merge).
        - The concatenation of Čech classes.
        - A new judgment at the meet trust tier.

        Parameters
        ----------
        ep1, ep2 : GenerationEpisode

        Returns
        -------
        GenerationEpisode
        """
        new_id = f"merged-{ep1.episode_id}-{ep2.episode_id}"
        merged_inhabitants = tuple(
            dict.fromkeys(
                list(ep1.inhabitants_generated) + list(ep2.inhabitants_generated)
            )
        )
        merged_cover = tuple(
            dict.fromkeys(
                list(ep1.cover_elements_visited) + list(ep2.cover_elements_visited)
            )
        )
        merged_trust = TrustAlgebra.meet(ep1.trust_tier, ep2.trust_tier)
        merged_cech = ep1.cech_class + ep2.cech_class
        j = make_judgment(
            context=f"merged:{ep1.generation_context}+{ep2.generation_context}",
            formula=f"merged({ep1.judgment[1]}, {ep2.judgment[1]})",
            assumptions=ep1.judgment[2] + ep2.judgment[2],
            evidence=ep1.judgment[3] + ep2.judgment[3],
            obstructions=merged_cech,
            blame="MemoryConsolidator",
            trust_tier=merged_trust,
            proof_obligations=ep1.judgment[7] + ep2.judgment[7],
        )
        return GenerationEpisode(
            episode_id=new_id,
            generation_context=f"{ep1.generation_context}|{ep2.generation_context}",
            inhabitants_generated=merged_inhabitants,
            cover_elements_visited=merged_cover,
            duration_ms=ep1.duration_ms + ep2.duration_ms,
            trust_tier=merged_trust,
            cech_class=merged_cech,
            judgment=j,
        )

    def consolidate(
        self, episodes: Sequence[GenerationEpisode]
    ) -> List[GenerationEpisode]:
        """Consolidate a list of episodes by merging overlapping pairs.

        Applies a greedy single-pass merge: pairs are sorted by decreasing
        overlap score and the first matching pair is merged.  The process
        repeats until no pairs exceed the threshold.

        Parameters
        ----------
        episodes : sequence of GenerationEpisode

        Returns
        -------
        list[GenerationEpisode]
        """
        current = list(episodes)
        changed = True
        while changed:
            changed = False
            pairs = self.detect_overlaps(current)
            if not pairs:
                break
            # Sort by decreasing overlap score
            pairs.sort(key=lambda t: t[2], reverse=True)
            id1, id2, _ = pairs[0]
            ep1 = next((e for e in current if e.episode_id == id1), None)
            ep2 = next((e for e in current if e.episode_id == id2), None)
            if ep1 is None or ep2 is None:
                break
            merged = self.merge_episodes(ep1, ep2)
            current = [e for e in current if e.episode_id not in (id1, id2)]
            current.append(merged)
            changed = True
        return current

    def split_episode(
        self,
        episode: GenerationEpisode,
        split_point: int,
    ) -> Tuple[GenerationEpisode, GenerationEpisode]:
        """Split an episode at a given inhabitant index.

        Useful for reversing an over-aggressive merge.

        Parameters
        ----------
        episode : GenerationEpisode
        split_point : int
            Index into inhabitants_generated at which to split.

        Returns
        -------
        tuple[GenerationEpisode, GenerationEpisode]
        """
        half = max(1, split_point)
        inh1 = episode.inhabitants_generated[:half]
        inh2 = episode.inhabitants_generated[half:]
        cov_half = len(episode.cover_elements_visited) // 2
        cov1 = episode.cover_elements_visited[:max(1, cov_half)]
        cov2 = episode.cover_elements_visited[max(1, cov_half):]

        def _make_part(suffix: str, inh: tuple, cov: tuple) -> GenerationEpisode:
            jj = make_judgment(
                context=episode.generation_context + suffix,
                formula=episode.judgment[1] + suffix,
                assumptions=episode.judgment[2],
                evidence=episode.judgment[3],
                obstructions=episode.cech_class,
                blame="MemoryConsolidator.split",
                trust_tier=episode.trust_tier,
                proof_obligations=episode.judgment[7],
            )
            return GenerationEpisode(
                episode_id=episode.episode_id + suffix,
                generation_context=episode.generation_context + suffix,
                inhabitants_generated=inh,
                cover_elements_visited=cov if cov else ("U_split",),
                duration_ms=episode.duration_ms / 2,
                trust_tier=episode.trust_tier,
                cech_class=episode.cech_class,
                judgment=jj,
            )

        return _make_part("-A", inh1, cov1), _make_part("-B", inh2, cov2)


# ===========================================================================
# GenerationStatistics
# ===========================================================================


class GenerationStatistics:
    """Collect and compute rich statistics over a stream of generation episodes.

    Statistics tracked
    ------------------
    - Duration distribution (mean, median, percentiles)
    - Trust tier distribution
    - Coverage histogram (cover element visit counts)
    - Episode arrival rate (episodes per second)
    - Total inhabitant count
    - Unique cover elements seen
    """

    def __init__(self) -> None:
        """Initialise an empty statistics collector."""
        self._episodes: List[GenerationEpisode] = []
        self._start_time: float = time.monotonic()

    def record(self, episode: GenerationEpisode) -> None:
        """Record a new episode for statistical tracking.

        Parameters
        ----------
        episode : GenerationEpisode
        """
        self._episodes.append(episode)

    def mean_duration(self) -> float:
        """Return the arithmetic mean of episode durations (ms).

        Returns
        -------
        float
        """
        if not self._episodes:
            return 0.0
        return statistics.mean(ep.duration_ms for ep in self._episodes)

    def median_duration(self) -> float:
        """Return the median episode duration (ms).

        Returns
        -------
        float
        """
        if not self._episodes:
            return 0.0
        return statistics.median(ep.duration_ms for ep in self._episodes)

    def percentile_duration(self, p: float) -> float:
        """Return the p-th percentile of episode durations (ms).

        Parameters
        ----------
        p : float
            Percentile in [0, 100].

        Returns
        -------
        float
        """
        if not self._episodes:
            return 0.0
        sorted_d = sorted(ep.duration_ms for ep in self._episodes)
        idx = max(0, min(len(sorted_d) - 1, int(math.ceil(p / 100 * len(sorted_d))) - 1))
        return sorted_d[idx]

    def trust_distribution(self) -> Dict[str, int]:
        """Return a frequency table of trust tiers.

        Returns
        -------
        dict[str, int]
        """
        dist: Dict[str, int] = defaultdict(int)
        for ep in self._episodes:
            dist[ep.trust_tier.name] += 1
        return dict(dist)

    def coverage_histogram(self) -> Dict[str, int]:
        """Return a histogram of cover element visit frequencies.

        Returns
        -------
        dict[str, int]
            Maps each cover element label to the number of episodes that
            visited it.
        """
        hist: Dict[str, int] = defaultdict(int)
        for ep in self._episodes:
            for elem in ep.cover_elements_visited:
                hist[elem] += 1
        return dict(hist)

    def episode_rate(self) -> float:
        """Return the average episode arrival rate in episodes/second.

        Returns
        -------
        float
        """
        elapsed = time.monotonic() - self._start_time
        if elapsed < 1e-9:
            return 0.0
        return len(self._episodes) / elapsed

    def total_inhabitants(self) -> int:
        """Return the total number of inhabitants across all episodes.

        Returns
        -------
        int
        """
        return sum(len(ep.inhabitants_generated) for ep in self._episodes)

    def unique_cover_elements(self) -> Tuple[str, ...]:
        """Return the set of unique cover elements seen across all episodes.

        Returns
        -------
        tuple[str, ...]
        """
        elems: set = set()
        for ep in self._episodes:
            elems.update(ep.cover_elements_visited)
        return tuple(sorted(elems))

    def summary_report(self) -> Dict[str, Any]:
        """Produce a comprehensive summary statistics report.

        Returns
        -------
        dict
            Contains all key metrics.
        """
        return {
            "total_episodes": len(self._episodes),
            "total_inhabitants": self.total_inhabitants(),
            "mean_duration_ms": self.mean_duration(),
            "median_duration_ms": self.median_duration(),
            "p95_duration_ms": self.percentile_duration(95),
            "p99_duration_ms": self.percentile_duration(99),
            "trust_distribution": self.trust_distribution(),
            "coverage_histogram": self.coverage_histogram(),
            "unique_cover_elements": self.unique_cover_elements(),
            "episode_rate_per_sec": self.episode_rate(),
        }


# ===========================================================================
# MemoryGarbageCollector
# ===========================================================================


class MemoryGarbageCollector:
    """Prune low-trust or stale episodes from an EpisodeStore.

    The GC operates in two phases:
    1. Mark : identify episodes that are reclaimable.
    2. Sweep: remove them from the store.

    An episode is reclaimable if its trust tier is strictly below the
    configured minimum tier OR if it appears in the explicit dead-set.

    After sweeping, compact() rebuilds the store's internal indices.
    """

    def __init__(
        self, store: "EpisodeStore", min_trust: TrustTier = TrustTier.REVIEWED
    ) -> None:
        """Initialise the GC.

        Parameters
        ----------
        store : EpisodeStore
            The episode store to manage.
        min_trust : TrustTier
            Episodes with trust < min_trust are candidates for collection.
        """
        self.store = store
        self.min_trust = min_trust
        self._marked: set = set()

    def is_reclaimable(self, episode: GenerationEpisode) -> bool:
        """Determine whether an episode should be collected.

        Parameters
        ----------
        episode : GenerationEpisode

        Returns
        -------
        bool
        """
        return TrustAlgebra.below_threshold(episode.trust_tier, self.min_trust)

    def mark_for_collection(self, episode_id: str) -> None:
        """Explicitly mark an episode for collection regardless of trust.

        Parameters
        ----------
        episode_id : str
        """
        self._marked.add(episode_id)

    def collect(self) -> List[str]:
        """Mark all reclaimable episodes in the store.

        Returns
        -------
        list[str]
            Episode IDs that have been marked for collection.
        """
        for ep in self.store.list_all():
            if self.is_reclaimable(ep):
                self._marked.add(ep.episode_id)
        return list(self._marked)

    def sweep(self) -> int:
        """Remove all marked episodes from the store.

        Returns
        -------
        int
            Number of episodes removed.
        """
        count = 0
        for eid in list(self._marked):
            if self.store.remove_episode(eid):
                count += 1
        self._marked.clear()
        return count

    def compact(self) -> int:
        """Compact the store by rebuilding indices from scratch.

        All live episodes are re-inserted into a fresh store instance, then
        the store's internal dictionaries are updated in place.

        Returns
        -------
        int
            Number of episodes retained after compaction.
        """
        live = self.store.list_all()
        # Reset store internals
        self.store._episodes.clear()
        self.store._cover_index.clear()
        self.store._type_index.clear()
        self.store._insertion_order.clear()
        for ep in live:
            self.store.add_episode(ep)
        return self.store.count()

    def report(self) -> Dict[str, Any]:
        """Return a report of what the GC would collect.

        Returns
        -------
        dict
            Keys: total_episodes, reclaimable_count, reclaimable_ids,
                  min_trust_threshold.
        """
        reclaimable = [
            ep.episode_id
            for ep in self.store.list_all()
            if self.is_reclaimable(ep)
        ]
        return {
            "total_episodes": self.store.count(),
            "reclaimable_count": len(reclaimable),
            "reclaimable_ids": reclaimable,
            "min_trust_threshold": self.min_trust.name,
        }


# ===========================================================================
# Module-level functions
# ===========================================================================


def record_episode(
    context: str,
    inhabitants: Sequence[str],
    cover_elements: Sequence[str],
    trust_tier: TrustTier = TrustTier.PROPOSAL,
    duration_ms: Optional[float] = None,
) -> GenerationEpisode:
    """Create and return a new GenerationEpisode from raw arguments.

    This is the primary factory function for episodes.  It generates a UUID,
    computes the Čech H¹ obstruction class for the given cover, and builds
    an 8-tuple judgment.

    Parameters
    ----------
    context : str
        Generation context identifier.
    inhabitants : sequence of str
        Identifiers of the generated inhabitants.
    cover_elements : sequence of str
        Labels of the cover elements visited.
    trust_tier : TrustTier
        Trust level of the episode output (default PROPOSAL).
    duration_ms : float, optional
        Elapsed time in milliseconds; defaults to 0.0.

    Returns
    -------
    GenerationEpisode
    """
    episode_id = str(uuid.uuid4())[:12]
    # Build a trivial cover: each consecutive pair gets transition 1+0j
    cover_dict: Dict[Tuple[str, str], complex] = {}
    elems = list(cover_elements)
    for i in range(len(elems)):
        for j in range(i + 1, len(elems)):
            cover_dict[(elems[i], elems[j])] = complex(1, 0)
    cech = CechCohomology(cover_dict)
    obstruction = cech.obstruction_class()
    judgment = make_judgment(
        context=context,
        formula=f"generated({', '.join(inhabitants)})",
        assumptions=(f"ctx:{context}",),
        evidence=tuple(f"cover:{e}" for e in elems),
        obstructions=obstruction if obstruction else (complex(1, 0),),
        blame="record_episode",
        trust_tier=trust_tier,
        proof_obligations=() if trust_tier >= TrustTier.VERIFIED else ("needs-review",),
    )
    return GenerationEpisode(
        episode_id=episode_id,
        generation_context=context,
        inhabitants_generated=tuple(inhabitants),
        cover_elements_visited=tuple(elems),
        duration_ms=duration_ms if duration_ms is not None else 0.0,
        trust_tier=trust_tier,
        cech_class=obstruction if obstruction else (complex(1, 0),),
        judgment=judgment,
    )


def assemble_memory(
    episodes: Sequence[GenerationEpisode],
    strategy: str = "sequential",
    trust_threshold: TrustTier = TrustTier.PROPOSAL,
) -> CumulativeGenerationMemory:
    """Assemble a CumulativeGenerationMemory from a sequence of episodes.

    Assembly strategies
    -------------------
    sequential : Episodes are combined in order; the combined trust is the
                 meet of all individual trust tiers.
    hierarchical : Episodes are grouped by cover element prefix and merged
                   within groups before combining across groups.
    optimistic  : The combined trust is the join (maximum) of all tiers.

    The Čech obstruction of the assembled memory is computed by concatenating
    the individual obstruction classes.

    Parameters
    ----------
    episodes : sequence of GenerationEpisode
        Source episodes to combine.
    strategy : str
        One of 'sequential', 'hierarchical', 'optimistic'.
    trust_threshold : TrustTier
        Minimum trust tier; episodes below this are excluded.

    Returns
    -------
    CumulativeGenerationMemory
    """
    # Filter by trust threshold
    filtered = [ep for ep in episodes if ep.trust_tier >= trust_threshold]
    if not filtered:
        # Return empty memory if nothing passes the threshold
        j = make_judgment(
            context="empty",
            formula="empty-memory",
            assumptions=(),
            evidence=(),
            obstructions=(),
            blame="assemble_memory",
            trust_tier=TrustTier.PROPOSAL,
            proof_obligations=("populate-memory",),
        )
        return CumulativeGenerationMemory(
            memory_id=str(uuid.uuid4())[:12],
            episodes=(),
            compressed_representation=(),
            total_generations=0,
            trust_tier=TrustTier.PROPOSAL,
            cech_obstruction=(),
            judgment=j,
        )

    # Compute aggregate trust
    if strategy == "optimistic":
        agg_trust = filtered[0].trust_tier
        for ep in filtered[1:]:
            agg_trust = TrustAlgebra.join(agg_trust, ep.trust_tier)
    else:
        agg_trust = filtered[0].trust_tier
        for ep in filtered[1:]:
            agg_trust = TrustAlgebra.meet(agg_trust, ep.trust_tier)

    # Aggregate Čech obstructions
    combined_cech: Tuple[complex, ...] = ()
    for ep in filtered:
        combined_cech = combined_cech + ep.cech_class

    # Build compressed representation
    compressor = MemoryCompressor()
    all_tokens = [tok for ep in filtered for tok in ep.inhabitants_generated]
    compressed = compressor.compress(all_tokens)

    episode_ids = tuple(ep.episode_id for ep in filtered)
    total_inh = sum(len(ep.inhabitants_generated) for ep in filtered)
    memory_id = hashlib.sha256(
        ("\n".join(episode_ids)).encode()
    ).hexdigest()[:16]

    j = make_judgment(
        context=strategy,
        formula=f"assembled-memory({strategy})",
        assumptions=tuple(f"episode:{eid}" for eid in episode_ids),
        evidence=tuple(f"inhabitant:{i}" for ep in filtered for i in ep.inhabitants_generated),
        obstructions=combined_cech,
        blame="assemble_memory",
        trust_tier=agg_trust,
        proof_obligations=() if agg_trust >= TrustTier.VERIFIED else ("verify-assembly",),
    )

    return CumulativeGenerationMemory(
        memory_id=memory_id,
        episodes=episode_ids,
        compressed_representation=tuple(compressed),
        total_generations=total_inh,
        trust_tier=agg_trust,
        cech_obstruction=combined_cech,
        judgment=j,
    )


def query_memory(
    memory: CumulativeGenerationMemory,
    query_pattern: str,
    top_k: int = 3,
    store: Optional["EpisodeStore"] = None,
) -> Tuple[GenerationEpisode, ...]:
    """Query a CumulativeGenerationMemory for relevant episodes.

    When a populated EpisodeStore is provided the retrieval uses BM25-style
    ranking.  Otherwise the function falls back to a simple substring filter
    over the memory's episode identifiers.

    Parameters
    ----------
    memory : CumulativeGenerationMemory
        The memory to query.
    query_pattern : str
        Free-text query or substring pattern.
    top_k : int
        Maximum number of results to return.
    store : EpisodeStore, optional
        Episode store to retrieve full episode objects from.

    Returns
    -------
    tuple[GenerationEpisode, ...]
    """
    if store is None:
        # Fallback: return placeholder episodes for matching episode_ids
        matching_ids = [
            eid for eid in memory.episodes if query_pattern.lower() in eid.lower()
        ][:top_k]
        results = []
        for eid in matching_ids:
            j = make_judgment(
                context="query-fallback",
                formula=f"stub-for-{eid}",
                assumptions=(),
                evidence=(),
                obstructions=(),
                blame="query_memory",
                trust_tier=memory.trust_tier,
                proof_obligations=("populate-store",),
            )
            results.append(
                GenerationEpisode(
                    episode_id=eid,
                    generation_context="query-fallback",
                    inhabitants_generated=(),
                    cover_elements_visited=(),
                    duration_ms=0.0,
                    trust_tier=memory.trust_tier,
                    cech_class=(),
                    judgment=j,
                )
            )
        return tuple(results)

    # Use BM25 retriever if store is available
    retriever = EpisodeRetriever()
    for eid in memory.episodes:
        ep = store.get_by_id(eid)
        if ep is not None:
            retriever.index_episode(ep)
    return retriever.retrieve(query_pattern, top_k=top_k)


def compress_memory(
    memory: CumulativeGenerationMemory,
    compression_ratio: float = 0.5,
) -> CumulativeGenerationMemory:
    """Return a new CumulativeGenerationMemory with a compressed representation.

    The function applies the MemoryCompressor to the existing compressed
    representation, targeting the given compression ratio.

    Parameters
    ----------
    memory : CumulativeGenerationMemory
        The memory to compress further.
    compression_ratio : float
        Target ratio (< 1 means more compression).  Actual ratio may differ.

    Returns
    -------
    CumulativeGenerationMemory
        A new frozen memory object with a shorter compressed_representation.
    """
    compressor = MemoryCompressor(min_run_length=max(1, int(1 / max(compression_ratio, 0.01))))
    tokens = list(memory.compressed_representation)
    compressed_tokens = compressor.compress(tokens)
    # Truncate to approximately the desired ratio
    target_len = max(1, int(len(tokens) * compression_ratio))
    compressed_tokens = compressed_tokens[:target_len]
    new_judgment = make_judgment(
        context=memory.judgment[0],
        formula=f"compressed({memory.judgment[1]})",
        assumptions=memory.judgment[2],
        evidence=memory.judgment[3],
        obstructions=memory.cech_obstruction,
        blame="compress_memory",
        trust_tier=memory.trust_tier,
        proof_obligations=memory.judgment[7],
    )
    return CumulativeGenerationMemory(
        memory_id=memory.memory_id + "-c",
        episodes=memory.episodes,
        compressed_representation=tuple(compressed_tokens),
        total_generations=memory.total_generations,
        trust_tier=memory.trust_tier,
        cech_obstruction=memory.cech_obstruction,
        judgment=new_judgment,
    )


# ===========================================================================
# __main__ block
# ===========================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("cumulative_generation_memory_assem — demo run")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. TrustAlgebra
    # ------------------------------------------------------------------
    print("\n[1] TrustAlgebra operations")
    ta = TrustAlgebra()
    t1, t2 = TrustTier.PROPOSAL, TrustTier.VERIFIED
    print(f"  meet({t1.name}, {t2.name}) = {TrustAlgebra.meet(t1, t2).name}")
    print(f"  join({t1.name}, {t2.name}) = {TrustAlgebra.join(t1, t2).name}")
    print(f"  PROPOSAL < PROOF_BACKED: {TrustTier.PROPOSAL < TrustTier.PROOF_BACKED}")
    print(f"  tier_distance(PROPOSAL, PROOF_BACKED): {TrustAlgebra.tier_distance(TrustTier.PROPOSAL, TrustTier.PROOF_BACKED)}")

    # ------------------------------------------------------------------
    # 2. CechCohomology
    # ------------------------------------------------------------------
    print("\n[2] CechCohomology")
    trivial_cover = {
        ("U0", "U1"): complex(1, 0),
        ("U1", "U2"): complex(1, 0),
        ("U0", "U2"): complex(1, 0),
    }
    cech_trivial = CechCohomology(trivial_cover)
    print(f"  Trivial cover is_trivial(): {cech_trivial.is_trivial()}")
    print(f"  Trivial obstruction_class(): {cech_trivial.obstruction_class()}")

    non_trivial_cover = {
        ("U0", "U1"): complex(1, 0.5),
        ("U1", "U2"): complex(0.8, 0.3),
        ("U0", "U2"): complex(1, 0),
    }
    cech_nt = CechCohomology(non_trivial_cover)
    print(f"  Non-trivial is_trivial(): {cech_nt.is_trivial()}")
    print(f"  Non-trivial obstruction class (first 2): {cech_nt.obstruction_class()[:2]}")

    # ------------------------------------------------------------------
    # 3. make_judgment
    # ------------------------------------------------------------------
    print("\n[3] make_judgment")
    j = make_judgment(
        context="demo-ctx",
        formula="demo-formula",
        assumptions=("A1",),
        evidence=("E1", "E2"),
        obstructions=(complex(1, 0),),
        blame="demo",
        trust_tier=TrustTier.VERIFIED,
        proof_obligations=("PO1",),
    )
    print(f"  Judgment context: {j[0]}, formula: {j[1]}, trust: {j[6].name}")

    # ------------------------------------------------------------------
    # 4. Module-level EPISODE_* constants
    # ------------------------------------------------------------------
    print("\n[4] Module-level episode constants")
    for ep in _ALL_EPISODES:
        print(f"  {ep.episode_id}: trust={ep.trust_tier.name}, "
              f"inhabitants={len(ep.inhabitants_generated)}, "
              f"cover={ep.cover_elements_visited}")

    # ------------------------------------------------------------------
    # 5. record_episode
    # ------------------------------------------------------------------
    print("\n[5] record_episode()")
    new_ep = record_episode(
        context="demo-context",
        inhabitants=["inh-new-1", "inh-new-2"],
        cover_elements=["U0", "U1", "U2"],
        trust_tier=TrustTier.REVIEWED,
        duration_ms=55.0,
    )
    print(f"  Created episode {new_ep.episode_id}, trust={new_ep.trust_tier.name}")
    print(f"  Cech class: {new_ep.cech_class}")

    # ------------------------------------------------------------------
    # 6. EpisodeStore
    # ------------------------------------------------------------------
    print("\n[6] EpisodeStore")
    store = EpisodeStore()
    for ep in _ALL_EPISODES:
        store.add_episode(ep)
    store.add_episode(new_ep)
    print(f"  Store count: {store.count()}")
    print(f"  get_by_id('ep-0003'): {store.get_by_id('ep-0003').episode_id}")
    print(f"  get_by_cover_element('U1'): {[e.episode_id for e in store.get_by_cover_element('U1')]}")
    print(f"  get_by_type(VERIFIED): {[e.episode_id for e in store.get_by_type(TrustTier.VERIFIED)]}")
    stats = store.get_statistics()
    print(f"  Statistics: total_episodes={stats['total_episodes']}, "
          f"total_inhabitants={stats['total_inhabitants']}, "
          f"mean_duration={stats['mean_duration_ms']:.1f}ms")
    store.remove_episode("ep-0001")
    print(f"  After removing ep-0001: {store.count()} episodes")

    # ------------------------------------------------------------------
    # 7. assemble_memory
    # ------------------------------------------------------------------
    print("\n[7] assemble_memory()")
    mem = assemble_memory(list(_ALL_EPISODES), strategy="sequential", trust_threshold=TrustTier.PROPOSAL)
    print(f"  memory_id: {mem.memory_id}")
    print(f"  episodes: {mem.episodes}")
    print(f"  total_generations: {mem.total_generations}")
    print(f"  trust_tier: {mem.trust_tier.name}")
    print(f"  compressed_representation (first 5): {mem.compressed_representation[:5]}")
    print(f"  cech_obstruction (first 3): {mem.cech_obstruction[:3]}")

    # ------------------------------------------------------------------
    # 8. MemoryAssembly dataclass
    # ------------------------------------------------------------------
    print("\n[8] MemoryAssembly dataclass")
    assembly = MemoryAssembly(
        assembly_id="asm-001",
        source_episodes=mem.episodes,
        assembly_strategy="sequential",
        resulting_memory_id=mem.memory_id,
        quality_score=0.87,
        trust_tier=mem.trust_tier,
    )
    print(f"  assembly_id: {assembly.assembly_id}, quality: {assembly.quality_score}")

    # ------------------------------------------------------------------
    # 9. query_memory
    # ------------------------------------------------------------------
    print("\n[9] query_memory()")
    # Re-add ep-0001 to store for querying
    store2 = EpisodeStore()
    for ep in _ALL_EPISODES:
        store2.add_episode(ep)
    results = query_memory(mem, "alpha", top_k=2, store=store2)
    print(f"  Query 'alpha' → {[r.episode_id for r in results]}")
    results2 = query_memory(mem, "epsilon", top_k=1, store=store2)
    print(f"  Query 'epsilon' → {[r.episode_id for r in results2]}")

    # ------------------------------------------------------------------
    # 10. compress_memory
    # ------------------------------------------------------------------
    print("\n[10] compress_memory()")
    compressed_mem = compress_memory(mem, compression_ratio=0.6)
    print(f"  Original compressed_representation len: {len(mem.compressed_representation)}")
    print(f"  After compress_memory len: {len(compressed_mem.compressed_representation)}")
    print(f"  memory_id: {compressed_mem.memory_id}")

    # ------------------------------------------------------------------
    # 11. MemoryCatalog dataclass
    # ------------------------------------------------------------------
    print("\n[11] MemoryCatalog dataclass")
    catalog_entries = tuple(
        (ep.episode_id, hashlib.sha256(ep.episode_id.encode()).hexdigest()[:8])
        for ep in _ALL_EPISODES
    )
    catalog = MemoryCatalog(
        catalog_id="cat-001",
        entries=catalog_entries,
        index_version=1,
        trust_tier=TrustTier.REVIEWED,
        obstruction_catalog=tuple(ep.cech_class for ep in _ALL_EPISODES),
    )
    print(f"  catalog_id: {catalog.catalog_id}, entries: {len(catalog.entries)}")

    # ------------------------------------------------------------------
    # 12. MemoryCompressor
    # ------------------------------------------------------------------
    print("\n[12] MemoryCompressor")
    mc = MemoryCompressor(min_run_length=2)
    tokens = ["inh-1", "inh-1", "inh-1", "inh-2", "inh-2", "inh-3"]
    compressed_toks = mc.compress(tokens)
    print(f"  Original: {tokens}")
    print(f"  Compressed: {compressed_toks}")
    decompressed = mc.decompress(compressed_toks)
    print(f"  Decompressed: {decompressed}")
    ratio = mc.estimate_compression_ratio(tokens)
    print(f"  Compression ratio: {ratio:.3f}")
    checksum = mc.compute_checksum(tokens)
    print(f"  Checksum: {checksum[:16]}...")

    # ------------------------------------------------------------------
    # 13. EpisodeRetriever (BM25)
    # ------------------------------------------------------------------
    print("\n[13] EpisodeRetriever (BM25)")
    retriever = EpisodeRetriever(k1=1.5, b=0.75)
    for ep in _ALL_EPISODES:
        retriever.index_episode(ep)
    ranked = retriever.rank_episodes("epsilon symmetry proof")
    print(f"  Top-3 episodes for 'epsilon symmetry proof':")
    for score, eid in ranked[:3]:
        print(f"    score={score:.4f}  episode_id={eid}")
    tf_val = retriever.compute_tf("epsilon", "ep-0005")
    idf_val = retriever.compute_idf("epsilon")
    print(f"  TF('epsilon','ep-0005')={tf_val:.2f}  IDF('epsilon')={idf_val:.4f}")

    # ------------------------------------------------------------------
    # 14. CumulativeIndex
    # ------------------------------------------------------------------
    print("\n[14] CumulativeIndex")
    cidx = CumulativeIndex()
    for ep in _ALL_EPISODES:
        for elem in ep.cover_elements_visited:
            cidx.insert(elem, ep.episode_id)
    print(f"  All keys: {cidx.all_keys()}")
    snap_id = cidx.snapshot()
    cidx.insert("U_new", "ep-0001")
    print(f"  After insert U_new, version={cidx.version}")
    cidx.restore(snap_id)
    print(f"  After restore, version={cidx.version}, U_new lookup={cidx.lookup('U_new')}")
    pruned = cidx.prune(["ep-0001"])
    print(f"  Pruned ep-0001: {pruned} entries removed")

    # ------------------------------------------------------------------
    # 15. MemoryConsolidator
    # ------------------------------------------------------------------
    print("\n[15] MemoryConsolidator")
    consolidator = MemoryConsolidator(overlap_threshold=0.2)
    overlaps = consolidator.detect_overlaps(list(_ALL_EPISODES))
    print(f"  Detected overlaps: {[(a[:8], b[:8], f'{s:.2f}') for a,b,s in overlaps]}")
    consolidated = consolidator.consolidate(list(_ALL_EPISODES))
    print(f"  After consolidation: {len(consolidated)} episodes (from {len(_ALL_EPISODES)})")
    split_a, split_b = consolidator.split_episode(EPISODE_3, split_point=1)
    print(f"  Split EPISODE_3 → {split_a.episode_id}, {split_b.episode_id}")

    # ------------------------------------------------------------------
    # 16. GenerationStatistics
    # ------------------------------------------------------------------
    print("\n[16] GenerationStatistics")
    gstats = GenerationStatistics()
    for ep in _ALL_EPISODES:
        gstats.record(ep)
    print(f"  mean_duration: {gstats.mean_duration():.1f}ms")
    print(f"  median_duration: {gstats.median_duration():.1f}ms")
    print(f"  p95_duration: {gstats.percentile_duration(95):.1f}ms")
    print(f"  trust_distribution: {gstats.trust_distribution()}")
    print(f"  coverage_histogram: {gstats.coverage_histogram()}")
    print(f"  total_inhabitants: {gstats.total_inhabitants()}")
    print(f"  unique_cover_elements: {gstats.unique_cover_elements()}")
    report = gstats.summary_report()
    print(f"  Summary keys: {list(report.keys())}")

    # ------------------------------------------------------------------
    # 17. MemoryGarbageCollector
    # ------------------------------------------------------------------
    print("\n[17] MemoryGarbageCollector")
    gc_store = EpisodeStore()
    for ep in _ALL_EPISODES:
        gc_store.add_episode(ep)
    gc = MemoryGarbageCollector(gc_store, min_trust=TrustTier.REVIEWED)
    gc_report = gc.report()
    print(f"  GC report: {gc_report}")
    marked = gc.collect()
    print(f"  Marked for collection: {marked}")
    removed_count = gc.sweep()
    print(f"  Swept {removed_count} episodes; store now has {gc_store.count()}")
    gc.mark_for_collection("ep-0002")
    gc.sweep()
    remaining = gc.compact()
    print(f"  After compact: {remaining} episodes")

    print("\n" + "=" * 70)
    print("Demo complete.")
    print("=" * 70)
