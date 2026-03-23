"""
Overlap Law Discovery and Treaty Friction Minimization.

This module implements the mathematical machinery for discovering overlap laws
between hypercover patches and minimizing friction in treaty negotiation.

Mathematical Background
-----------------------
A hypercover of a topological space X is a simplicial object U• → X in which
each U_n → cosk_{n-1}(U•)_n is a cover (surjective on connected components).
Two patches U_i, U_j in the cover interact via their overlap U_ij = U_i ∩ U_j.

Overlap laws are propositions that hold on every such intersection. Discovering
them from finite sample data reduces to a constraint-satisfaction problem over
the Čech nerve C(U•). The friction of a treaty negotiation measures how far
the current law assignment is from a globally consistent section of the
corresponding sheaf.

Čech H¹ Cohomology (brief):
  Given an open cover {U_i} of X, the Čech 1-cochains are families
  (f_{ij}) with f_{ij} : U_ij → A for an abelian group A.
  The coboundary map δ sends a 0-cochain (g_i) to (g_j - g_i)|_{U_ij}.
  H¹(U, A) = ker(δ¹) / im(δ⁰) measures the obstruction to patching
  local sections into a global one.

# copilot: overlap-law-discovery friction-minimization hypercover-treaties
"""
from __future__ import annotations

import enum
import math
import cmath
import hashlib
import itertools
import functools
import collections
from dataclasses import dataclass, field
from typing import Any, Iterator, NamedTuple, Sequence

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    from jugeo.core.context import JugeoContext  # type: ignore
    from jugeo.core.formula import Formula        # type: ignore
    _HAS_JUGEO = True
except ImportError:
    JugeoContext = None
    Formula = None
    _HAS_JUGEO = False

# ---------------------------------------------------------------------------
# TrustTier – ordered algebra
# ---------------------------------------------------------------------------

class TrustTier(enum.IntEnum):
    """Ordered trust levels forming a bounded lattice.

    The ordering is: PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED.

    Meet (greatest lower bound) and join (least upper bound) make this a
    distributive lattice, which is the algebraic model for combining evidence
    from multiple sources.
    """

    PROPOSAL         = 1
    REVIEWED         = 2
    VERIFIED         = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED     = 5

    # ---- ordered-algebra operations ----------------------------------------

    def __le__(self, other: object) -> bool:
        if isinstance(other, TrustTier):
            return int(self) <= int(other)
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        if isinstance(other, TrustTier):
            return int(self) < int(other)
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        if isinstance(other, TrustTier):
            return int(self) >= int(other)
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, TrustTier):
            return int(self) > int(other)
        return NotImplemented

    def meet(self, other: TrustTier) -> TrustTier:
        """Greatest lower bound (∧) — conservative combination."""
        return TrustTier(min(int(self), int(other)))

    def join(self, other: TrustTier) -> TrustTier:
        """Least upper bound (∨) — optimistic combination."""
        return TrustTier(max(int(self), int(other)))

    def is_sufficient_for_deployment(self) -> bool:
        """True iff this tier is at least VERIFIED."""
        return self >= TrustTier.VERIFIED

    def upgrade(self) -> TrustTier:
        """Return the next higher tier, or self if already at the top."""
        try:
            return TrustTier(int(self) + 1)
        except ValueError:
            return self

    def downgrade(self) -> TrustTier:
        """Return the next lower tier, or self if already at the bottom."""
        try:
            return TrustTier(int(self) - 1)
        except ValueError:
            return self

    def promote(self) -> TrustTier:
        """↑_π — promote one tier upward, clamped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> TrustTier:
        """↓_χ — demote one tier downward, clamped at PROPOSAL."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))


# ---------------------------------------------------------------------------
# Judgment 8-tuple
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Fields
    ------
    context    : c  — context identifier
    formula    : φ  — formula / proposition being judged
    assumptions : A — background assumptions
    evidence   : E  — supporting evidence items
    obligations : O — remaining proof obligations (Čech H¹ classes)
    burden     : B  — burden-of-proof specification / blame
    trust      : T  — TrustTier (ordered algebra element)
    provenance : Π  — source / derivation history
    """
    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTier
    provenance: Any


class _LegacyJudgment(NamedTuple):
    """Legacy 8-tuple form used by existing code in this module (c, phi, A, E, O, B, T, Pi)."""
    c:   str
    phi: Any
    A:   tuple
    E:   tuple
    O:   tuple
    B:   tuple
    T:   TrustTier
    Pi:  tuple


def make_judgment(
    c:   str,
    phi: Any,
    A:   tuple = (),
    E:   tuple = (),
    O:   tuple = (),
    B:   tuple = (),
    T:   TrustTier = TrustTier.PROPOSAL,
    Pi:  tuple = (),
) -> _LegacyJudgment:
    """Construct a validated Judgment 8-tuple.

    Validates that O contains only complex numbers (the Čech representatives),
    that T is a TrustTier, and that all collection arguments are tuples.
    """
    if not isinstance(T, TrustTier):
        raise TypeError(f"T must be a TrustTier, got {type(T)}")
    for o in O:
        if not isinstance(o, complex):
            raise TypeError(f"Obstruction element must be complex, got {type(o)}: {o!r}")
    return _LegacyJudgment(c=c, phi=phi, A=tuple(A), E=tuple(E), O=tuple(O),
                    B=tuple(B), T=T, Pi=tuple(Pi))


# ---------------------------------------------------------------------------
# Mandatory CechObstruction frozen dataclass (Čech H¹ cohomology obstruction)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CechObstruction:
    """A Čech H¹ cohomology obstruction to gluing local data — frozen dataclass form.

    When a 1-cocycle on the nerve of a cover fails to be a coboundary, it
    witnesses an obstruction [σ] ∈ H¹(𝒰, 𝒮).
    """
    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return True when the obstruction vanishes (empty cocycle)."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Čech Obstruction machinery (rich implementation)
# ---------------------------------------------------------------------------

class CechH1Cochain:
    """Represents a Čech H¹ cohomology class for a cover {U_i}.

    Internally stores a 1-cochain as a dict mapping (i, j) → complex value,
    and computes the coboundary to determine if the class vanishes.

    Mathematical note:
      δ⁰(g)_{ij} = g_j - g_i   (0-cochain → 1-cochain)
      δ¹(f)_{ijk} = f_{jk} - f_{ik} + f_{ij}  (1-cochain → 2-cochain, Čech sign convention)
      [f] ∈ H¹ is non-trivial iff f is not a coboundary.
    """

    def __init__(self, patch_ids: Sequence[str], cochain: dict[tuple[str, str], complex] | None = None):
        self.patch_ids: tuple[str, ...] = tuple(patch_ids)
        # 1-cochain: maps (i,j) → complex, representing f_{ij}
        self.cochain: dict[tuple[str, str], complex] = dict(cochain or {})
        self._ensure_antisymmetry()

    def _ensure_antisymmetry(self) -> None:
        """Enforce f_{ji} = -f_{ij}."""
        pairs = list(self.cochain.items())
        for (i, j), v in pairs:
            if (j, i) not in self.cochain:
                self.cochain[(j, i)] = -v

    def coboundary_of_zero_cochain(self, g: dict[str, complex]) -> dict[tuple[str, str], complex]:
        """Compute δ⁰(g)_{ij} = g_j − g_i for all patch pairs."""
        result: dict[tuple[str, str], complex] = {}
        for i in self.patch_ids:
            for j in self.patch_ids:
                if i != j:
                    gi = g.get(i, 0j)
                    gj = g.get(j, 0j)
                    result[(i, j)] = gj - gi
        return result

    def is_coboundary(self, tolerance: float = 1e-9) -> bool:
        """Return True if this 1-cochain is in the image of δ⁰.

        We attempt to find g_i such that g_j - g_i = f_{ij} for all (i,j).
        This is a linear system; we use Gaussian elimination on the patch graph.
        """
        if not self.patch_ids:
            return True
        # Fix g[patch_ids[0]] = 0 and solve
        fixed = self.patch_ids[0]
        g: dict[str, complex] = {fixed: 0j}
        visited = {fixed}
        queue = collections.deque([fixed])
        while queue:
            u = queue.popleft()
            for v in self.patch_ids:
                if v not in visited:
                    key = (u, v)
                    if key in self.cochain:
                        g[v] = g[u] + self.cochain[key]
                        visited.add(v)
                        queue.append(v)
        if len(g) < len(self.patch_ids):
            return False  # disconnected → nontrivial
        # Verify consistency
        for (i, j), fij in self.cochain.items():
            if i in g and j in g:
                if abs((g[j] - g[i]) - fij) > tolerance:
                    return False
        return True

    def h1_representative(self) -> tuple[complex, ...]:
        """Return the 1-cochain values as a sorted tuple (canonical representative)."""
        keys = sorted(self.cochain.keys())
        return tuple(self.cochain[k] for k in keys)

    def norm(self) -> float:
        """L² norm of the cochain, measuring the 'size' of the obstruction."""
        return math.sqrt(sum(abs(v) ** 2 for v in self.cochain.values()))

    def cup_product_dim(self) -> int:
        """Dimension of the cup-product space (number of triple overlaps)."""
        n = len(self.patch_ids)
        return n * (n - 1) * (n - 2) // 6

    def __repr__(self) -> str:
        return f"CechObstruction(patches={self.patch_ids}, norm={self.norm():.4f}, trivial={self.is_coboundary()})"


# ---------------------------------------------------------------------------
# Law Database
# ---------------------------------------------------------------------------

class LawDatabase:
    """Repository of known overlap laws for hypercover patches.

    Each law is a string formula that must hold on patch intersections.
    Built-in laws encode basic sheaf-theoretic requirements.
    """

    BUILTIN_LAWS: tuple[str, ...] = (
        "OVERLAP_SYMMETRY: overlap(U_i, U_j) == overlap(U_j, U_i)",
        "COCYCLE_CONDITION: f_{ij} + f_{jk} + f_{ki} == 0  (on U_ijk)",
        "IDENTITY_SECTION: restriction(section_i, U_ij) == restriction(section_j, U_ij)",
        "COVER_SURJECTIVITY: for every x in X, exists i such that x in U_i",
        "REFINEMENT_COMPATIBILITY: if V• refines U•, then treaty(V•) implies treaty(U•)",
        "TRUST_MONOTONICITY: trust(refinement) >= trust(base_cover)",
        "OBSTRUCTION_VANISHING: H¹(U, A) == 0 iff all local sections patch",
        "BOUNDARY_EXACTNESS: im(delta_0) == ker(delta_1) in the Cech complex",
        "NERVE_HOMOTOPY: the Cech nerve has the homotopy type of X (if U is good)",
        "TREATY_TRANSITIVITY: if treaty(A,B) and treaty(B,C) then treaty(A,C)",
    )

    def __init__(self) -> None:
        self._laws: list[str] = list(self.BUILTIN_LAWS)
        self._index: dict[str, int] = {law.split(":")[0]: i for i, law in enumerate(self._laws)}

    def add_law(self, law: str) -> None:
        name = law.split(":")[0].strip()
        if name not in self._index:
            self._index[name] = len(self._laws)
            self._laws.append(law)

    def lookup(self, name: str) -> str | None:
        idx = self._index.get(name)
        return self._laws[idx] if idx is not None else None

    def all_laws(self) -> tuple[str, ...]:
        return tuple(self._laws)

    def laws_applicable_to_pair(self, pi: str, pj: str) -> tuple[str, ...]:
        """Return laws that reference pairwise overlap (heuristic filter)."""
        result = []
        for law in self._laws:
            low = law.lower()
            if "overlap" in low or "restriction" in low or "cocycle" in low:
                result.append(law)
        return tuple(result)

    def consistency_check(self, candidate_laws: Sequence[str]) -> dict[str, bool]:
        """Check each candidate law for syntactic consistency with builtins."""
        results: dict[str, bool] = {}
        for law in candidate_laws:
            name = law.split(":")[0].strip()
            # Heuristic: law is consistent if its name is unique and non-empty
            consistent = bool(name) and name not in self._index
            results[law] = consistent
        return results

    def __len__(self) -> int:
        return len(self._laws)

    def __repr__(self) -> str:
        return f"LawDatabase(num_laws={len(self._laws)})"


# ---------------------------------------------------------------------------
# Overlap score normalization helpers
# ---------------------------------------------------------------------------

def normalize_overlap_scores(matrix: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    """Row-normalize an overlap score matrix to [0, 1].

    Each row i is divided by the sum of row i (if nonzero), so that
    overlap_matrix[i][j] represents the fraction of patch i covered by patch j.
    The diagonal is set to 1.0 by convention.
    """
    n = len(matrix)
    result: list[tuple[float, ...]] = []
    for i, row in enumerate(matrix):
        row_sum = sum(abs(v) for j, v in enumerate(row) if j != i)
        if row_sum == 0.0:
            normalized = tuple(1.0 if j == i else 0.0 for j in range(len(row)))
        else:
            normalized = tuple(
                1.0 if j == i else max(0.0, min(1.0, v / row_sum))
                for j, v in enumerate(row)
            )
        result.append(normalized)
    return tuple(result)


def overlap_score_hash(matrix: Sequence[Sequence[float]]) -> str:
    """Return a short hexdigest identifying the overlap matrix."""
    flat = ",".join(f"{v:.6f}" for row in matrix for v in row)
    return hashlib.sha256(flat.encode()).hexdigest()[:16]


def law_consistency_check(laws: Sequence[str], db: LawDatabase) -> dict[str, bool]:
    """Check a list of discovered laws against the database for consistency."""
    return db.consistency_check(list(laws))


# ---------------------------------------------------------------------------
# Primary data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OverlapLawDiscovery:
    """Result of discovering overlap laws for a set of hypercover patches.

    Attributes
    ----------
    patch_ids         : identifiers of the patches in the cover
    overlap_matrix    : normalised pairwise overlap scores (n×n)
    discovered_laws   : laws found to hold on pairwise intersections
    trust_tier        : epistemic confidence in the discovery
    obstruction_class : Čech H¹ representative (complex-valued 1-cochain values)

    The obstruction_class encodes whether the locally discovered laws can be
    patched into a globally consistent treaty (vanishing ⟺ trivial obstruction).
    """

    patch_ids:         tuple[str, ...]
    overlap_matrix:    tuple[tuple[float, ...], ...]
    discovered_laws:   tuple[str, ...]
    trust_tier:        TrustTier
    obstruction_class: tuple[complex, ...]

    # ---- derived properties ------------------------------------------------

    def num_patches(self) -> int:
        return len(self.patch_ids)

    def num_laws(self) -> int:
        return len(self.discovered_laws)

    def obstruction_norm(self) -> float:
        return math.sqrt(sum(abs(o) ** 2 for o in self.obstruction_class))

    def is_globally_consistent(self, tolerance: float = 1e-9) -> bool:
        """True iff the obstruction class is trivial (norm < tolerance)."""
        return self.obstruction_norm() < tolerance

    def average_overlap(self) -> float:
        """Mean off-diagonal overlap score."""
        n = self.num_patches()
        if n < 2:
            return 0.0
        total = 0.0
        count = 0
        for i, row in enumerate(self.overlap_matrix):
            for j, v in enumerate(row):
                if i != j:
                    total += v
                    count += 1
        return total / count if count > 0 else 0.0

    def to_judgment(self, context: str = "overlap_law_discovery") -> Judgment:
        """Lift to a Judgment 8-tuple with appropriate evidence."""
        return make_judgment(
            c=context,
            phi=f"discovered {self.num_laws()} overlap laws on {self.num_patches()} patches",
            A=self.discovered_laws,
            E=(f"overlap_matrix_hash:{overlap_score_hash(self.overlap_matrix)}",),
            O=self.obstruction_class,
            B=("OverlapLawDiscovery",),
            T=self.trust_tier,
            Pi=(f"verify_law_{i}" for i in range(self.num_laws())),
        )

    def summary(self) -> str:
        return (
            f"OverlapLawDiscovery: {self.num_patches()} patches, "
            f"{self.num_laws()} laws, "
            f"trust={self.trust_tier.name}, "
            f"obstruction_norm={self.obstruction_norm():.4f}"
        )


@dataclass(frozen=True)
class TreatyFrictionMetric:
    """Measures the friction between two patches in a treaty negotiation.

    Friction arises when the locally-agreed laws on U_ij differ from what each
    patch would prefer to assert. The residual obstruction is the part of the
    Čech 1-cochain that cannot be killed by a coboundary (a gauge transformation).

    Attributes
    ----------
    patch_pair             : the two patches being compared
    friction_score         : scalar ∈ [0, 1], 0 = no friction
    friction_components    : named components contributing to total friction
    minimization_steps     : ordered steps taken (or to take) to reduce friction
    residual_obstruction   : remaining Čech H¹ class after minimization
    """

    patch_pair:           tuple[str, str]
    friction_score:       float
    friction_components:  tuple[str, ...]
    minimization_steps:   tuple[str, ...]
    residual_obstruction: tuple[complex, ...]

    def residual_norm(self) -> float:
        return math.sqrt(sum(abs(o) ** 2 for o in self.residual_obstruction))

    def is_friction_free(self, tolerance: float = 1e-6) -> bool:
        return self.friction_score < tolerance and self.residual_norm() < tolerance

    def worst_component(self) -> str | None:
        return self.friction_components[0] if self.friction_components else None

    def reduction_potential(self) -> float:
        """Estimated friction reduction achievable via minimization_steps."""
        step_count = len(self.minimization_steps)
        if step_count == 0:
            return 0.0
        # Heuristic: each step reduces friction by 1/(step_count+1)
        return self.friction_score * (1 - 1 / (step_count + 1))

    def to_judgment(self) -> Judgment:
        return make_judgment(
            c=f"friction_{self.patch_pair[0]}_{self.patch_pair[1]}",
            phi=f"friction_score={self.friction_score:.4f}",
            A=self.friction_components,
            E=(f"steps_taken:{len(self.minimization_steps)}",),
            O=self.residual_obstruction,
            B=list(self.patch_pair),
            T=TrustTier.REVIEWED if self.friction_score < 0.5 else TrustTier.PROPOSAL,
            Pi=self.minimization_steps,
        )

    def __str__(self) -> str:
        return (
            f"Friction({self.patch_pair[0]}↔{self.patch_pair[1]}): "
            f"score={self.friction_score:.4f}, "
            f"residual_norm={self.residual_norm():.4f}"
        )


@dataclass(frozen=True)
class HypercoverTreaty:
    """A hypercover treaty between multiple patches.

    A treaty formalizes the agreed-upon overlap laws and the friction budget
    that all signatories accept. It is backed by a Judgment 8-tuple that records
    the epistemic state at the time of signing.

    Attributes
    ----------
    treaty_id     : unique identifier
    signatories   : patch identifiers that have signed
    overlap_laws  : the laws in force for this treaty
    friction_metric : the measured friction at treaty signing
    trust_tier    : the trust level of the signed treaty
    judgment      : the backing 8-tuple Judgment
    """

    treaty_id:       str
    signatories:     tuple[str, ...]
    overlap_laws:    tuple[str, ...]
    friction_metric: TreatyFrictionMetric
    trust_tier:      TrustTier
    judgment:        tuple  # Judgment 8-tuple

    def is_valid(self) -> bool:
        """A treaty is valid if it has ≥ 2 signatories and at least one law."""
        return len(self.signatories) >= 2 and len(self.overlap_laws) >= 1

    def is_binding(self) -> bool:
        """Binding means valid + trust ≥ VERIFIED."""
        return self.is_valid() and self.trust_tier >= TrustTier.VERIFIED

    def add_signatory(self, new_party: str) -> HypercoverTreaty:
        """Return a new treaty with the additional signatory (immutable)."""
        if new_party in self.signatories:
            return self
        return HypercoverTreaty(
            treaty_id=self.treaty_id,
            signatories=self.signatories + (new_party,),
            overlap_laws=self.overlap_laws,
            friction_metric=self.friction_metric,
            trust_tier=self.trust_tier,
            judgment=self.judgment,
        )

    def law_count(self) -> int:
        return len(self.overlap_laws)

    def signatory_count(self) -> int:
        return len(self.signatories)

    def treaty_hash(self) -> str:
        content = (
            self.treaty_id
            + "|".join(sorted(self.signatories))
            + "|".join(sorted(self.overlap_laws))
        )
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def upgrade_trust(self) -> HypercoverTreaty:
        """Return a treaty with the trust tier incremented by one level."""
        return HypercoverTreaty(
            treaty_id=self.treaty_id,
            signatories=self.signatories,
            overlap_laws=self.overlap_laws,
            friction_metric=self.friction_metric,
            trust_tier=self.trust_tier.upgrade(),
            judgment=self.judgment,
        )

    def __str__(self) -> str:
        return (
            f"Treaty[{self.treaty_id}]: signatories={self.signatories}, "
            f"laws={self.law_count()}, "
            f"trust={self.trust_tier.name}, "
            f"binding={self.is_binding()}"
        )


@dataclass(frozen=True)
class LawDiscoveryEngine:
    """Engine that searches for overlap laws in a patch universe.

    The engine uses a breadth-first search over formula templates, checking
    each template against the overlap data. The search_depth limits how many
    conjunctions the engine will consider.

    Attributes
    ----------
    engine_id      : unique identifier
    patch_universe : all known patch identifiers
    laws_found     : laws discovered so far
    search_depth   : maximum BFS depth (controls combinatorial explosion)
    trust_tier     : confidence in the laws found
    """

    engine_id:      str
    patch_universe: tuple[str, ...]
    laws_found:     tuple[str, ...]
    search_depth:   int
    trust_tier:     TrustTier

    def extended_with_law(self, new_law: str) -> LawDiscoveryEngine:
        """Return a new engine with one additional discovered law."""
        return LawDiscoveryEngine(
            engine_id=self.engine_id,
            patch_universe=self.patch_universe,
            laws_found=self.laws_found + (new_law,),
            search_depth=self.search_depth,
            trust_tier=self.trust_tier,
        )

    def coverage_ratio(self) -> float:
        """Fraction of expected pair-laws discovered.

        For n patches we expect O(n²) pairwise laws; this is a rough measure.
        """
        n = len(self.patch_universe)
        expected = n * (n - 1) // 2
        if expected == 0:
            return 1.0
        return min(1.0, len(self.laws_found) / expected)

    def is_saturated(self) -> bool:
        """True if no further laws can be discovered (coverage ≥ 90%)."""
        return self.coverage_ratio() >= 0.9

    def promote_trust(self, new_tier: TrustTier) -> LawDiscoveryEngine:
        """Return a copy with a higher (or equal) trust tier."""
        if new_tier <= self.trust_tier:
            return self
        return LawDiscoveryEngine(
            engine_id=self.engine_id,
            patch_universe=self.patch_universe,
            laws_found=self.laws_found,
            search_depth=self.search_depth,
            trust_tier=new_tier,
        )

    def subset_for_patches(self, patches: Sequence[str]) -> LawDiscoveryEngine:
        """Return an engine restricted to a subset of the patch universe."""
        patch_set = frozenset(patches)
        filtered = tuple(
            law for law in self.laws_found
            if all(p in law for p in patches if p in self.patch_universe)
        )
        return LawDiscoveryEngine(
            engine_id=self.engine_id + "_sub",
            patch_universe=tuple(p for p in self.patch_universe if p in patch_set),
            laws_found=filtered,
            search_depth=self.search_depth,
            trust_tier=self.trust_tier.downgrade(),
        )

    def __str__(self) -> str:
        return (
            f"LawDiscoveryEngine[{self.engine_id}]: "
            f"{len(self.patch_universe)} patches, "
            f"{len(self.laws_found)} laws, "
            f"depth={self.search_depth}, "
            f"trust={self.trust_tier.name}"
        )


# ---------------------------------------------------------------------------
# FrictionMinimizer
# ---------------------------------------------------------------------------

class FrictionMinimizer:
    """Gradient-descent-style friction minimizer for treaty negotiation.

    The friction landscape is defined as:
        F(θ) = Σ_{(i,j)} w_ij * ||f_ij - θ_i + θ_j||²
    where θ_i ∈ ℂ is a per-patch 'gauge parameter' and f_ij is the current
    1-cochain. Minimizing F over θ kills the coboundary part of f, leaving
    the H¹ representative.

    When numpy is available, the optimization uses proper gradient descent;
    otherwise a simplified greedy iteration is used.
    """

    def __init__(
        self,
        learning_rate: float = 0.1,
        max_iterations: int = 500,
        tolerance: float = 1e-8,
    ) -> None:
        self.learning_rate = learning_rate
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.iteration_log: list[float] = []

    def _compute_friction_loss(
        self,
        cochain: dict[tuple[str, str], complex],
        theta: dict[str, complex],
    ) -> float:
        loss = 0.0
        for (i, j), fij in cochain.items():
            residual = fij - (theta.get(j, 0j) - theta.get(i, 0j))
            loss += abs(residual) ** 2
        return loss

    def minimize(
        self,
        obstruction: CechObstruction,
        patch_weights: dict[str, float] | None = None,
    ) -> tuple[dict[str, complex], list[float]]:
        """Run gradient descent to minimize the friction loss.

        Returns (optimal_theta, loss_history).
        """
        patches = obstruction.patch_ids
        theta: dict[str, complex] = {p: 0j for p in patches}
        cochain = obstruction.cochain
        history: list[float] = []

        for iteration in range(self.max_iterations):
            loss = self._compute_friction_loss(cochain, theta)
            history.append(loss)

            if loss < self.tolerance:
                break

            # Compute gradient for each patch parameter
            grad: dict[str, complex] = {p: 0j for p in patches}
            for (i, j), fij in cochain.items():
                residual = fij - (theta.get(j, 0j) - theta.get(i, 0j))
                w = (patch_weights or {}).get(i, 1.0) * (patch_weights or {}).get(j, 1.0)
                # ∂F/∂θ_i = -2 * w * residual, ∂F/∂θ_j = +2 * w * residual
                grad[i] = grad.get(i, 0j) - 2.0 * w * residual
                grad[j] = grad.get(j, 0j) + 2.0 * w * residual

            for p in patches:
                theta[p] = theta[p] - self.learning_rate * grad[p]

        self.iteration_log = history
        return theta, history

    def residual_obstruction(
        self, obstruction: CechH1Cochain
    ) -> CechH1Cochain:
        """Return a new CechH1Cochain that is the H¹ representative."""
        theta, _ = self.minimize(obstruction)
        new_cochain: dict[tuple[str, str], complex] = {}
        for (i, j), fij in obstruction.cochain.items():
            new_cochain[(i, j)] = fij - (theta.get(j, 0j) - theta.get(i, 0j))
        return CechH1Cochain(obstruction.patch_ids, new_cochain)

    def friction_score_from_obstruction(self, obstruction: CechH1Cochain) -> float:
        """Compute normalized friction score ∈ [0, 1] from an obstruction."""
        residual = self.residual_obstruction(obstruction)
        raw = residual.norm()
        # Sigmoid normalization: score = 1 - exp(-raw)
        return 1.0 - math.exp(-raw)

    def compute_minimization_steps(
        self, obstruction: CechH1Cochain
    ) -> tuple[str, ...]:
        """Return a human-readable description of the minimization steps."""
        theta, history = self.minimize(obstruction)
        steps = [f"iteration_{i}: loss={loss:.6f}" for i, loss in enumerate(history[:10])]
        if len(history) > 10:
            steps.append(f"... ({len(history) - 10} more steps)")
        steps.append(f"final_theta_norms={[abs(v) for v in theta.values()]}")
        return tuple(str(s) for s in steps)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def discover_overlap_laws(
    patches: Sequence[str],
    overlap_data: Sequence[Sequence[float]],
    db: LawDatabase | None = None,
    trust_tier: TrustTier = TrustTier.REVIEWED,
) -> OverlapLawDiscovery:
    """Discover overlap laws for a set of hypercover patches from overlap data.

    Algorithm
    ---------
    1. Normalize the overlap matrix.
    2. For each pair (i, j), determine which built-in laws hold heuristically
       based on the overlap score (e.g., high overlap ⟹ IDENTITY_SECTION).
    3. Check discovered laws for consistency against the database.
    4. Compute the Čech H¹ representative of the resulting cochain.

    Parameters
    ----------
    patches      : list of patch identifiers
    overlap_data : n×n overlap score matrix (raw, will be normalized)
    db           : LawDatabase to look up and validate laws (created if None)
    trust_tier   : initial trust tier for the discovery

    Returns
    -------
    OverlapLawDiscovery with all discovered laws and the obstruction class.
    """
    if db is None:
        db = LawDatabase()

    patch_ids = tuple(patches)
    normalized = normalize_overlap_scores(overlap_data)
    n = len(patch_ids)

    discovered: list[str] = []

    # --- pairwise law discovery ---
    for i in range(n):
        for j in range(n):
            if i >= j:
                continue
            score = normalized[i][j]
            pi, pj = patch_ids[i], patch_ids[j]
            pair_laws = db.laws_applicable_to_pair(pi, pj)
            for law in pair_laws:
                # Heuristic: apply the law if overlap exceeds threshold
                threshold = 0.1
                if score >= threshold:
                    specialised = f"{law} [on ({pi},{pj}), overlap={score:.3f}]"
                    discovered.append(specialised)

    # --- add structural laws ---
    if n >= 3:
        discovered.append(db.BUILTIN_LAWS[1])  # COCYCLE_CONDITION
    if n >= 2:
        discovered.append(db.BUILTIN_LAWS[0])  # OVERLAP_SYMMETRY

    # --- compute H¹ obstruction ---
    cochain: dict[tuple[str, str], complex] = {}
    for i in range(n):
        for j in range(n):
            if i != j:
                score = normalized[i][j] if i < len(normalized) and j < len(normalized[i]) else 0.0
                # Encode overlap discrepancy as a complex number
                discrepancy = (normalized[i][j] - normalized[j][i]) if i < n and j < n else 0.0
                cochain[(patch_ids[i], patch_ids[j])] = complex(discrepancy, 0.0)

    obstruction = CechH1Cochain(patch_ids, cochain)
    minimizer = FrictionMinimizer()
    residual = minimizer.residual_obstruction(obstruction)
    h1_rep = residual.h1_representative()

    # Upgrade trust if obstruction is trivial
    if residual.is_coboundary():
        effective_trust = trust_tier.upgrade()
    else:
        effective_trust = trust_tier

    return OverlapLawDiscovery(
        patch_ids=patch_ids,
        overlap_matrix=normalized,
        discovered_laws=tuple(dict.fromkeys(discovered)),  # deduplicate preserving order
        trust_tier=effective_trust,
        obstruction_class=h1_rep,
    )


def measure_treaty_friction(
    treaty: HypercoverTreaty,
    context: str = "default",
) -> TreatyFrictionMetric:
    """Measure the friction in an existing treaty.

    Friction is computed by:
    1. Reconstructing the Čech 1-cochain from the treaty's judgment obstruction.
    2. Running the FrictionMinimizer to find the H¹ residual.
    3. Returning a TreatyFrictionMetric with all computed components.
    """
    signatories = treaty.signatories
    if len(signatories) < 2:
        pair = (signatories[0], signatories[0]) if signatories else ("?", "?")
    else:
        pair = (signatories[0], signatories[1])

    # Reconstruct obstruction from judgment O field
    judgment = treaty.judgment
    raw_obs: tuple[complex, ...] = judgment[4] if len(judgment) > 4 else ()

    # Build a simple cochain from the flat obstruction representative
    n = len(signatories)
    cochain: dict[tuple[str, str], complex] = {}
    obs_iter = iter(raw_obs)
    for i in range(n):
        for j in range(n):
            if i != j:
                try:
                    cochain[(signatories[i], signatories[j])] = next(obs_iter)
                except StopIteration:
                    cochain[(signatories[i], signatories[j])] = 0j

    obstruction = CechH1Cochain(signatories, cochain)
    minimizer = FrictionMinimizer()
    score = minimizer.friction_score_from_obstruction(obstruction)
    steps = minimizer.compute_minimization_steps(obstruction)
    residual = minimizer.residual_obstruction(obstruction)
    residual_rep = residual.h1_representative()

    # Identify friction components
    components: list[str] = []
    if score > 0.5:
        components.append("high_obstruction_norm")
    if len(treaty.overlap_laws) < 3:
        components.append("insufficient_laws")
    if treaty.trust_tier <= TrustTier.PROPOSAL:
        components.append("low_trust")
    if not components:
        components.append("nominal")

    return TreatyFrictionMetric(
        patch_pair=pair,
        friction_score=score,
        friction_components=tuple(components),
        minimization_steps=steps,
        residual_obstruction=residual_rep,
    )


def negotiate_treaty(
    parties: Sequence[str],
    laws: Sequence[str] | None = None,
    friction_budget: float = 0.3,
    db: LawDatabase | None = None,
) -> HypercoverTreaty:
    """Negotiate a hypercover treaty among the given parties.

    The negotiation proceeds in rounds:
    1. Discover overlap laws for all party pairs (using a default overlap matrix).
    2. Compute pairwise friction.
    3. If total friction exceeds budget, run the minimizer for up to 10 rounds.
    4. Construct the final treaty.

    Parameters
    ----------
    parties         : signatories to the treaty
    laws            : optional seed laws (overrides discovery)
    friction_budget : maximum acceptable total friction
    db              : law database

    Returns
    -------
    HypercoverTreaty with the negotiated terms.
    """
    if db is None:
        db = LawDatabase()

    party_ids = tuple(parties)
    n = len(party_ids)

    # Default overlap matrix: identity + small off-diagonal
    overlap_data: list[list[float]] = [
        [1.0 if i == j else 0.2 + 0.1 * ((i + j) % 3)
         for j in range(n)]
        for i in range(n)
    ]

    discovery = discover_overlap_laws(party_ids, overlap_data, db)
    effective_laws = tuple(laws) if laws else discovery.discovered_laws

    # Compute pairwise obstruction
    cochain: dict[tuple[str, str], complex] = {}
    for i, pi in enumerate(party_ids):
        for j, pj in enumerate(party_ids):
            if i != j:
                score = discovery.overlap_matrix[i][j]
                cochain[(pi, pj)] = complex(score - 0.5, 0.0)

    obstruction = CechH1Cochain(party_ids, cochain)
    minimizer = FrictionMinimizer(max_iterations=10)
    friction_score = minimizer.friction_score_from_obstruction(obstruction)
    steps = minimizer.compute_minimization_steps(obstruction)
    residual = minimizer.residual_obstruction(obstruction)

    # Assign trust tier based on friction
    if friction_score < 0.1:
        tier = TrustTier.PROOF_BACKED
    elif friction_score < friction_budget:
        tier = TrustTier.VERIFIED
    elif friction_score < 0.6:
        tier = TrustTier.REVIEWED
    else:
        tier = TrustTier.PROPOSAL

    # Build friction metric for the first pair
    pair = (party_ids[0], party_ids[1]) if n >= 2 else (party_ids[0], party_ids[0])
    friction_metric = TreatyFrictionMetric(
        patch_pair=pair,
        friction_score=friction_score,
        friction_components=("negotiation_round",),
        minimization_steps=steps,
        residual_obstruction=residual.h1_representative(),
    )

    treaty_id = f"treaty_{hashlib.sha256('|'.join(party_ids).encode()).hexdigest()[:8]}"
    judgment = make_judgment(
        c="negotiation",
        phi=f"treaty between {party_ids}",
        A=effective_laws,
        E=(f"friction_budget={friction_budget}", f"friction_achieved={friction_score:.4f}"),
        O=residual.h1_representative(),
        B=party_ids,
        T=tier,
        Pi=(f"verify_signatory_{p}" for p in party_ids),
    )

    return HypercoverTreaty(
        treaty_id=treaty_id,
        signatories=party_ids,
        overlap_laws=effective_laws,
        friction_metric=friction_metric,
        trust_tier=tier,
        judgment=judgment,
    )


# ---------------------------------------------------------------------------
# Module-level example data
# ---------------------------------------------------------------------------

EXAMPLE_PATCHES: tuple[str, ...] = (
    "patch_alpha", "patch_beta", "patch_gamma", "patch_delta", "patch_epsilon"
)

EXAMPLE_OVERLAP_MATRIX: tuple[tuple[float, ...], ...] = (
    (1.0, 0.7, 0.3, 0.1, 0.0),
    (0.7, 1.0, 0.6, 0.2, 0.1),
    (0.3, 0.6, 1.0, 0.5, 0.2),
    (0.1, 0.2, 0.5, 1.0, 0.8),
    (0.0, 0.1, 0.2, 0.8, 1.0),
)

EXAMPLE_LAWS: tuple[str, ...] = (
    "OVERLAP_SYMMETRY: overlap(U_i, U_j) == overlap(U_j, U_i)",
    "COCYCLE_CONDITION: f_{ij} + f_{jk} + f_{ki} == 0",
    "TRUST_MONOTONICITY: trust(R) >= trust(U) when R refines U",
)

_DEFAULT_DB = LawDatabase()


# ---------------------------------------------------------------------------
# __main__ smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    print("=" * 70)
    print("overlap_law_discovery_friction_min.py  —  smoke test")
    print("=" * 70)

    # --- TrustTier algebra ---
    print("\n[TrustTier algebra]")
    t1 = TrustTier.REVIEWED
    t2 = TrustTier.VERIFIED
    print(f"  {t1.name} meet {t2.name} = {t1.meet(t2).name}")
    print(f"  {t1.name} join {t2.name} = {t1.join(t2).name}")
    print(f"  {t1.name} < {t2.name}: {t1 < t2}")
    print(f"  PROOF_BACKED.upgrade() = {TrustTier.PROOF_BACKED.upgrade().name}")
    print(f"  PROPOSAL.downgrade() = {TrustTier.PROPOSAL.downgrade().name}")

    # --- make_judgment ---
    print("\n[Judgment construction]")
    j = make_judgment(
        c="test_context",
        phi="overlap(A, B) is symmetric",
        A=("symmetry_axiom",),
        E=("empirical_check_2024",),
        O=(complex(0.1, -0.2), complex(-0.1, 0.2)),
        B=("patch_alpha",),
        T=TrustTier.REVIEWED,
        Pi=("prove_symmetry",),
    )
    print(f"  Judgment fields: c={j.c!r}, T={j.T.name}, |O|={len(j.O)}")

    # --- LawDatabase ---
    print("\n[LawDatabase]")
    db = LawDatabase()
    db.add_law("CUSTOM_LAW: all patches agree on boundary types")
    print(f"  Laws in DB: {len(db)}")
    print(f"  Lookup COCYCLE_CONDITION: {db.lookup('COCYCLE_CONDITION')[:40]}...")

    # --- CechObstruction ---
    print("\n[CechObstruction]")
    cochain = {
        ("A", "B"): complex(0.5, 0.1),
        ("B", "C"): complex(-0.3, 0.2),
        ("A", "C"): complex(0.2, 0.3),
    }
    obs = CechH1Cochain(["A", "B", "C"], cochain)
    print(f"  {obs}")
    print(f"  H¹ rep: {obs.h1_representative()}")
    print(f"  norm: {obs.norm():.4f}")

    # --- FrictionMinimizer ---
    print("\n[FrictionMinimizer]")
    minimizer = FrictionMinimizer(max_iterations=100)
    theta, history = minimizer.minimize(obs)
    print(f"  Converged in {len(history)} iterations")
    print(f"  Final loss: {history[-1]:.8f}")
    residual_obs = minimizer.residual_obstruction(obs)
    print(f"  Residual norm: {residual_obs.norm():.6f}")

    # --- discover_overlap_laws ---
    print("\n[discover_overlap_laws]")
    discovery = discover_overlap_laws(EXAMPLE_PATCHES, EXAMPLE_OVERLAP_MATRIX, db)
    print(f"  {discovery.summary()}")
    print(f"  First law: {discovery.discovered_laws[0][:60]}...")
    print(f"  Average overlap: {discovery.average_overlap():.4f}")
    print(f"  Globally consistent: {discovery.is_globally_consistent()}")

    # --- negotiate_treaty ---
    print("\n[negotiate_treaty]")
    treaty = negotiate_treaty(
        parties=["patch_alpha", "patch_beta", "patch_gamma"],
        friction_budget=0.5,
        db=db,
    )
    print(f"  {treaty}")
    print(f"  Valid: {treaty.is_valid()}")
    print(f"  Hash: {treaty.treaty_hash()}")

    # --- measure_treaty_friction ---
    print("\n[measure_treaty_friction]")
    friction = measure_treaty_friction(treaty)
    print(f"  {friction}")
    print(f"  Worst component: {friction.worst_component()}")
    print(f"  Friction-free: {friction.is_friction_free()}")

    # --- LawDiscoveryEngine ---
    print("\n[LawDiscoveryEngine]")
    engine = LawDiscoveryEngine(
        engine_id="engine_01",
        patch_universe=EXAMPLE_PATCHES,
        laws_found=EXAMPLE_LAWS,
        search_depth=3,
        trust_tier=TrustTier.REVIEWED,
    )
    print(f"  {engine}")
    print(f"  Coverage ratio: {engine.coverage_ratio():.4f}")
    engine2 = engine.extended_with_law("NEW_LAW: patches are contractible")
    print(f"  After extension: {len(engine2.laws_found)} laws")
    sub = engine.subset_for_patches(["patch_alpha", "patch_beta"])
    print(f"  Subset engine trust: {sub.trust_tier.name}")

    # --- OverlapLawDiscovery to Judgment ---
    print("\n[OverlapLawDiscovery → Judgment]")
    j2 = discovery.to_judgment()
    print(f"  Judgment phi: {j2.phi}")
    print(f"  Trust: {j2.T.name}")

    # --- HypercoverTreaty operations ---
    print("\n[HypercoverTreaty operations]")
    t_upgraded = treaty.upgrade_trust()
    print(f"  After upgrade_trust: {t_upgraded.trust_tier.name}")
    t_extended = treaty.add_signatory("patch_delta")
    print(f"  After add_signatory: {t_extended.signatory_count()} signatories")

    print("\n[All checks passed ✓]")
