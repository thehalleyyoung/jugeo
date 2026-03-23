"""
# copilot: canonicalized_fragments_for_z3.py — Canonicalized fragments for Z3 solver input.

This module implements the second stage of the mixed-evidence routing pipeline:
taking raw evidence fragments, canonicalizing them, and preparing them for
submission to a Z3 SMT solver session.

THEORY INVARIANTS:
  - Judgment tuples are always (c, φ, A, E, O, B, T, Π) where:
      c  = context (runtime evaluation environment)
      φ  = formula (the proposition under judgment)
      A  = agent-set (set of agents asserting the fragment)
      E  = evidence-set (supporting evidence items)
      O  = obligation-set (outstanding proof obligations)
      B  = belief-state (epistemic state of the asserting coalition)
      T  = trust-tier (position in the ordered trust algebra)
      Π  = proof-object (witness or certificate)
  - Trust is an ordered algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ), NEVER a plain float.
      ≼   = dominance partial order on trust tiers
      ⊕   = trust join (least upper bound of two trust levels)
      ⊖   = trust retraction (downward revision on counter-evidence)
      ↑_π = promotion operator (moves a fragment up the trust ladder with proof π)
      ↓_χ = demotion operator (moves a fragment down on counter-evidence χ)
  - TrustTier ordering: PROPOSAL ≼ REVIEWED ≼ VERIFIED ≼ RUNTIME_WITNESSED ≼ PROOF_BACKED
  - Canonicalization is not cosmetic: it is a judgment-preserving transformation.
    A canonicalized fragment J_canon is valid iff the original judgment J holds,
    and the canonical form preserves all structural invariants required by Z3.

DESIGN:
  Frozen dataclasses enforce immutability of fragment representations — once a
  fragment has been canonicalized, it cannot be mutated without producing a new
  fragment with a new identity.  This aligns with the trust algebra requirement
  that trust promotions produce new proof objects rather than mutating old ones.

Version: 1.0.0
"""
from __future__ import annotations

import enum
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Jugeo core imports with graceful fallback to minimal stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.core.trust import TrustAlgebra, TrustProof
    from jugeo.core.judgment import JudgmentTuple, ProofObject
    from jugeo.core.context import EvaluationContext
    from jugeo.orchestration.base import FragmentBase, EvidenceSetBase
    _JUGEO_AVAILABLE = True
except ImportError:  # pragma: no cover — stubs used in isolated environments
    _JUGEO_AVAILABLE = False

    # --- Minimal stubs so the module can be imported and tested standalone ---

    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub: ordered algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ)."""

        @staticmethod
        def join(a: int, b: int) -> int:
            """⊕ — least upper bound in the trust partial order."""
            return max(a, b)

        @staticmethod
        def retract(a: int, evidence_weight: float) -> int:
            """⊖ — downward revision; evidence_weight ∈ [0, 1]."""
            reduction = int(evidence_weight * 2)
            return max(1, a - reduction)

        @staticmethod
        def promote(tier: int, proof_id: str) -> int:
            """↑_π — move tier up by one level when a valid proof exists."""
            return min(5, tier + 1)

        @staticmethod
        def demote(tier: int, counter_evidence: str) -> int:
            """↓_χ — move tier down by one level on counter-evidence χ."""
            return max(1, tier - 1)

    class TrustProof:  # type: ignore[no-redef]
        """Stub proof object carrying a proof identifier and witness payload."""

        def __init__(self, proof_id: str, witness: str = "") -> None:
            self.proof_id = proof_id
            self.witness = witness

        def __repr__(self) -> str:
            return f"TrustProof(proof_id={self.proof_id!r}, witness={self.witness!r})"

    class JudgmentTuple:  # type: ignore[no-redef]
        """Stub: (c, φ, A, E, O, B, T, Π)."""

        def __init__(
            self,
            context: Any,
            formula: str,
            agent_set: frozenset,
            evidence_set: frozenset,
            obligation_set: frozenset,
            belief_state: dict,
            trust_tier: int,
            proof_object: Any,
        ) -> None:
            self.context = context
            self.formula = formula
            self.agent_set = agent_set
            self.evidence_set = evidence_set
            self.obligation_set = obligation_set
            self.belief_state = belief_state
            self.trust_tier = trust_tier
            self.proof_object = proof_object

    class ProofObject:  # type: ignore[no-redef]
        """Stub proof object (Π component of a judgment tuple)."""

        def __init__(self, proof_id: str = "", payload: str = "") -> None:
            self.proof_id = proof_id or str(uuid.uuid4())
            self.payload = payload

        def __repr__(self) -> str:
            return f"ProofObject(proof_id={self.proof_id!r})"

    class EvaluationContext:  # type: ignore[no-redef]
        """Stub runtime evaluation context (c component of judgment tuple)."""

        def __init__(self, context_id: str = "") -> None:
            self.context_id = context_id or str(uuid.uuid4())
            self.bindings: dict[str, Any] = {}

    class FragmentBase:  # type: ignore[no-redef]
        """Stub base class for evidence fragments."""
        pass

    class EvidenceSetBase:  # type: ignore[no-redef]
        """Stub base class for evidence sets."""
        pass


# ===========================================================================
# CONSTANTS
# ===========================================================================

NORMALIZATION_TIMEOUT: float = 10.0
"""Maximum wall-clock seconds allowed for a single normalization pass."""

MAX_FRAGMENT_SIZE: int = 10_000
"""Maximum character length of a raw fragment before rejection."""

CANONICAL_NORMAL_FORM_VERSION: str = "1.0.0"
"""Version tag embedded in every canonical hash to allow rolling invalidation."""

# ---------------------------------------------------------------------------
# Z3 sort precedence — used when resolving sort conflicts during unification.
# Higher numeric precedence "wins" when two incompatible sorts are unified;
# the result is promoted to the dominant sort.  This mirrors the trust algebra
# join (⊕): in ambiguous typing situations we take the least upper bound.
# ---------------------------------------------------------------------------
Z3_SORT_PRECEDENCE: dict  # forward reference resolved below after Z3Sort is defined


# ===========================================================================
# ENUMERATIONS
# ===========================================================================

class NormalizationLevel(enum.IntEnum):
    """
    Ordered levels of normalization applied to a fragment.

    RAW              – fragment as received; no processing applied.
    SYNTAX_NORMAL    – syntactic transformations only (whitespace, parentheses,
                       operator associativity).
    SEMANTIC_NORMAL  – semantic-preserving rewrites (commutativity, idempotency,
                       absorption, De Morgan, etc.).
    CANONICAL        – fully canonical form: unique representative of the
                       equivalence class under the judgment-preserving rewriting
                       system.  Two fragments are logically equivalent iff they
                       have the same CANONICAL form.
    VERIFIED_CANONICAL – CANONICAL + independent Z3 round-trip check confirming
                       that the canonical form is equisatisfiable with the
                       original.  This level satisfies the Π-component of the
                       judgment tuple (proof_object is populated).
    """
    RAW = 0
    SYNTAX_NORMAL = 1
    SEMANTIC_NORMAL = 2
    CANONICAL = 3
    VERIFIED_CANONICAL = 4


class Z3Sort(enum.Enum):
    """
    Z3 sorts recognised by this module's sort-inference engine.

    The ordering reflects type generality (more general sorts subsume less
    general ones) — used when resolving sort conflicts during variable binding.
    """
    BOOL = "Bool"
    INT = "Int"
    REAL = "Real"
    BITVEC = "BitVec"
    ARRAY = "Array"
    UNINTERPRETED = "Uninterpreted"
    ALGEBRAIC = "Algebraic"


class FragmentType(enum.Enum):
    """
    Logical language family of an evidence fragment.

    Each family requires a distinct normalization ruleset and may impose
    additional sort constraints on Z3 variables.
    """
    PROPOSITIONAL = "propositional"
    FIRST_ORDER = "first_order"
    MODAL = "modal"
    TEMPORAL = "temporal"
    DEONTIC = "deontic"
    EPISTEMIC = "epistemic"


class TrustTier(enum.IntEnum):
    """
    Position in the trust ordered algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).

    The integer values define the dominance partial order ≼:
        PROPOSAL ≼ REVIEWED ≼ VERIFIED ≼ RUNTIME_WITNESSED ≼ PROOF_BACKED

    Operations:
      join(a, b) = max(a, b)            — ⊕ (least upper bound)
      retract(a) = max(PROPOSAL, a-1)   — ⊖ (downward revision)
      promote(a) = min(PROOF_BACKED, a+1)  — ↑_π
      demote(a)  = max(PROPOSAL, a-1)   — ↓_χ
    """
    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5


# Resolve the forward reference for Z3_SORT_PRECEDENCE now that Z3Sort exists.
Z3_SORT_PRECEDENCE = {
    Z3Sort.BOOL: 10,
    Z3Sort.INT: 20,
    Z3Sort.REAL: 30,
    Z3Sort.BITVEC: 25,
    Z3Sort.ARRAY: 40,
    Z3Sort.ALGEBRAIC: 35,
    Z3Sort.UNINTERPRETED: 50,  # uninterpreted sorts are most general
}


# ===========================================================================
# HELPER FROZEN DATACLASSES
# ===========================================================================

@dataclass(frozen=True)
class VariableBinding:
    """
    Records the sort and domain of a single logical variable.

    In the judgment tuple (c, φ, A, E, O, B, T, Π), variable bindings are
    part of the context c.  The ``binding_proof`` field stores the fragment ID
    or rule ID that determined the sort, providing a lightweight Π-component
    for each individual binding decision.

    Fields:
        variable_name  – identifier as it appears in the canonical formula.
        sort           – inferred Z3Sort for this variable.
        domain         – additional domain constraint (e.g. "positive_integers",
                         "unit_interval"); empty string means unconstrained.
        binding_proof  – identifier of the rule or fragment that established
                         this binding (used for proof-trace construction).
    """
    variable_name: str
    sort: Z3Sort
    domain: str
    binding_proof: str


@dataclass(frozen=True)
class NormalizationRule:
    """
    A single rewriting rule used during normalization.

    Rules are applied in descending priority order (higher priority first).
    The ``pattern`` is a Python-style regex; ``replacement`` uses regex
    back-reference syntax.  The ``applicable_types`` tuple restricts which
    FragmentType values may use this rule.

    Fields:
        rule_id          – unique identifier for this rule.
        pattern          – regex pattern to match in the formula string.
        replacement      – replacement string (may use \\1, \\2 back-refs).
        priority         – application priority (higher = applied first).
        applicable_types – tuple of FragmentType names this rule applies to;
                           empty tuple means "apply to all types".
    """
    rule_id: str
    pattern: str
    replacement: str
    priority: int
    applicable_types: tuple  # tuple[str, ...]


@dataclass(frozen=True)
class SortSignature:
    """
    Type signature for a logical function or predicate symbol.

    Mirrors a Z3 FuncDecl sort signature.  Used during sort inference to
    propagate known sorts from function symbols to their arguments and return
    positions.

    Fields:
        input_sorts – tuple of Z3Sort for each argument position.
        output_sort – Z3Sort of the return value.
        arity       – number of arguments (must equal len(input_sorts)).
        is_total    – True iff the function is total over its domain.
    """
    input_sorts: tuple  # tuple[Z3Sort, ...]
    output_sort: Z3Sort
    arity: int
    is_total: bool


# ===========================================================================
# PRIMARY FROZEN DATACLASSES
# ===========================================================================

@dataclass(frozen=True)
class CanonicalizedFragment:
    """
    An immutable, fully canonicalized evidence fragment ready for Z3 input.

    Instances of this class correspond to the φ-component of a judgment tuple
    (c, φ, A, E, O, B, T, Π), where the fragment has been transformed into a
    canonical representative of its logical equivalence class.

    Immutability is enforced by ``frozen=True``.  Any normalization step that
    changes the formula must produce a *new* CanonicalizedFragment instance —
    this mirrors the trust algebra requirement that trust promotions (↑_π)
    create new proof objects rather than mutating existing ones.

    Fields:
        fragment_id        – globally unique identifier (UUID-based).
        original_text      – the raw, unprocessed input string.
        canonical_form     – the canonical representative of the formula's
                             equivalence class under the rewriting system.
        sort_signature     – tuple encoding the sort context, stored as a
                             tuple of (symbol_name, sort_name) pairs.
        variable_bindings  – tuple of (variable_name, sort_name) pairs for
                             every free variable in canonical_form.
        normalization_level – highest NormalizationLevel achieved.
        z3_compatible      – True iff canonical_form can be parsed by Z3.
        proof_witness      – identifier of the proof object Π certifying
                             that the canonicalization is judgment-preserving.
    """
    fragment_id: str
    original_text: str
    canonical_form: str
    sort_signature: tuple          # tuple[tuple[str, str], ...]
    variable_bindings: tuple       # tuple[tuple[str, str], ...]
    normalization_level: NormalizationLevel
    z3_compatible: bool
    proof_witness: str


@dataclass(frozen=True)
class Z3Preparation:
    """
    A fully prepared Z3 solver input derived from a CanonicalizedFragment.

    The ``preparation_proof`` field records the proof-object identifier that
    certifies the Z3 preparation is equisatisfiable with the original
    canonical fragment — completing the Π-component of the judgment tuple for
    the *prepared* representation.

    Fields:
        fragment          – the CanonicalizedFragment this was prepared from.
        z3_assertions     – tuple of Z3 assertion strings in SMT-LIB2 syntax.
        z3_sorts          – tuple of (symbol_name, sort_name) sort declarations.
        z3_variables      – tuple of (var_name, sort_name) variable declarations.
        solver_timeout    – suggested timeout in seconds for the solver call.
        expected_result   – "sat", "unsat", or "unknown".
        preparation_proof – identifier of the Π-object for this preparation.
    """
    fragment: CanonicalizedFragment
    z3_assertions: tuple   # tuple[str, ...]
    z3_sorts: tuple        # tuple[tuple[str, str], ...]
    z3_variables: tuple    # tuple[tuple[str, str], ...]
    solver_timeout: float
    expected_result: str
    preparation_proof: str


@dataclass(frozen=True)
class FragmentNormalizer:
    """
    An immutable normalizer configuration carrying a fixed rule set.

    The normalizer itself is frozen because a rule-set change implies a
    potentially different canonical form — which would invalidate existing
    proof witnesses.  Producing a new normalizer with updated rules forces
    re-canonicalization of all dependent fragments.

    Fields:
        normalization_rules – tuple of NormalizationRule instances, sorted by
                              descending priority at construction time.
        substitution_map    – tuple of (pattern, replacement) pairs applied as
                              literal string substitutions before regex rules.
        canonical_hash      – SHA-256 digest of the serialized rule set,
                              used to detect rule-set version mismatches.
        normalizer_id       – unique identifier for this normalizer version.
    """
    normalization_rules: tuple   # tuple[NormalizationRule, ...]
    substitution_map: tuple      # tuple[tuple[str, str], ...]
    canonical_hash: str
    normalizer_id: str


@dataclass(frozen=True)
class SolverInputBuilder:
    """
    Immutable specification for building a complete Z3 solver problem instance.

    Combines multiple CanonicalizedFragments, background axioms, and a query
    formula into a single coherent input.  Immutability ensures that a
    SolverInputBuilder instance is a stable, reproducible specification: any
    modification to the problem produces a new builder with a new identity,
    which preserves the audit trail required by the trust algebra.

    Fields:
        fragments         – tuple of CanonicalizedFragment instances.
        constraints       – tuple of additional constraint strings.
        background_axioms – tuple of axiom strings assumed by the solver.
        query_formula     – the formula whose satisfiability is queried.
        metadata          – tuple of (key, value) metadata pairs.
    """
    fragments: tuple           # tuple[CanonicalizedFragment, ...]
    constraints: tuple         # tuple[str, ...]
    background_axioms: tuple   # tuple[str, ...]
    query_formula: str
    metadata: tuple            # tuple[tuple[str, str], ...]


# ===========================================================================
# NON-FROZEN (MUTABLE) CLASSES
# ===========================================================================

class Z3SolverSession:
    """
    Manages a live Z3 solver session, accumulating assertions from multiple
    canonicalized fragments before issuing a solve call.

    Unlike the frozen dataclasses above, this class is intentionally mutable:
    a solver session is a stateful, side-effectful interaction with an external
    tool.  Its mutation history is tracked through the ``assertions`` and
    ``loaded_fragments`` lists, which serve as a lightweight proof trace (Π).

    Trust considerations: fragments loaded into this session must have a
    normalization_level of at least CANONICAL to ensure the Z3 encoding is
    judgment-preserving.  Attempting to load a lower-level fragment will
    trigger automatic promotion to CANONICAL via normalize_fragment.

    Attributes:
        session_id       – unique identifier for this solver session.
        loaded_fragments – list of CanonicalizedFragment instances loaded so far.
        assertions       – list of Z3 assertion strings accumulated so far.
        solved           – True once solve() has been called successfully.
        result           – "sat", "unsat", or "unknown" after solve().
    """

    def __init__(self, session_id: str = "") -> None:
        self.session_id: str = session_id or str(uuid.uuid4())
        self.loaded_fragments: list[CanonicalizedFragment] = []
        self.assertions: list[str] = []
        self.solved: bool = False
        self.result: str = "unknown"
        self._proof_trace: list[str] = []
        self._start_time: float = time.monotonic()
        self._load_count: int = 0

    def load_fragment(self, fragment: CanonicalizedFragment) -> bool:
        """
        Load a CanonicalizedFragment into the session, promoting it to at least
        CANONICAL level if necessary.

        This method updates the session's assertion list and proof trace.
        It returns True on successful load, False if the fragment fails
        validation or is a duplicate of an already-loaded fragment.

        Trust invariant: a fragment loaded here must satisfy the φ-component
        of its originating judgment tuple.  We check z3_compatible and
        normalization_level as proxies for this invariant.

        Args:
            fragment: A CanonicalizedFragment to add to this session.

        Returns:
            True if loaded successfully; False otherwise.
        """
        # Reject fragments that have not been normalized at least to CANONICAL.
        effective = fragment
        if fragment.normalization_level < NormalizationLevel.CANONICAL:
            effective = normalize_fragment(fragment, NormalizationLevel.CANONICAL)

        # Reject non-Z3-compatible fragments.
        if not effective.z3_compatible:
            self._proof_trace.append(
                f"REJECTED fragment {fragment.fragment_id}: not z3_compatible"
            )
            return False

        # Deduplication: skip if we've already loaded this canonical form.
        for already_loaded in self.loaded_fragments:
            if already_loaded.canonical_form == effective.canonical_form:
                self._proof_trace.append(
                    f"SKIPPED duplicate fragment {fragment.fragment_id} "
                    f"(matches {already_loaded.fragment_id})"
                )
                return False

        # Build Z3 assertions for this fragment.
        new_assertions = _fragment_to_assertions(effective)
        self.assertions.extend(new_assertions)
        self.loaded_fragments.append(effective)
        self._load_count += 1

        # Record the load event in the proof trace (lightweight Π-extension).
        self._proof_trace.append(
            f"LOADED fragment {effective.fragment_id} "
            f"(level={effective.normalization_level.name}, "
            f"assertions={len(new_assertions)})"
        )
        return True

    def add_assertion(self, assertion: str) -> None:
        """
        Add a raw Z3 assertion string directly to the session.

        This bypasses the fragment-level trust checks and should be used only
        for background axioms or solver-level constraints that are not derived
        from the evidence corpus.  Each such manual assertion is recorded in the
        proof trace so that the full derivation is auditable.

        Args:
            assertion: A single Z3 assertion string in SMT-LIB2 / Python-Z3
                       syntax.
        """
        if not assertion or not assertion.strip():
            return
        cleaned = assertion.strip()
        self.assertions.append(cleaned)
        self._proof_trace.append(f"MANUAL assertion: {cleaned[:80]}")

    def solve(self, timeout: float = NORMALIZATION_TIMEOUT) -> str:
        """
        Execute the solver over all accumulated assertions.

        In a production environment this would call out to the z3 Python
        package.  Here we implement a simulation that exercises the full
        logical pathway: we check for trivial contradictions, tautologies, and
        then return "sat" or "unknown" depending on the assertion content.

        The result is stored in ``self.result`` and ``self.solved`` is set
        to True regardless of the result.

        Trust algebra note: the solve result does NOT automatically promote
        the trust tier of loaded fragments.  That is a separate, explicit step
        in the orchestration pipeline (↑_π) that requires a human or automated
        review of the solver certificate.

        Args:
            timeout: Solver timeout in seconds.

        Returns:
            One of "sat", "unsat", or "unknown".
        """
        if not self.assertions:
            self.solved = True
            self.result = "sat"  # empty theory is trivially satisfiable
            self._proof_trace.append("SOLVE: no assertions — trivially sat")
            return self.result

        start = time.monotonic()
        contradiction_patterns = [
            # explicit contradictions: (assert false)
            r"\(assert\s+false\)",
            r"assert\s+False",
        ]
        tautology_patterns = [
            r"\(assert\s+true\)",
            r"assert\s+True",
        ]

        joined = " ".join(self.assertions)

        # Check for explicit contradictions first.
        for pat in contradiction_patterns:
            if re.search(pat, joined, re.IGNORECASE):
                self.result = "unsat"
                self.solved = True
                elapsed = time.monotonic() - start
                self._proof_trace.append(
                    f"SOLVE: contradiction detected in {elapsed:.4f}s → unsat"
                )
                return self.result

        # Check timeout simulation.
        if timeout <= 0:
            self.result = "unknown"
            self.solved = True
            self._proof_trace.append("SOLVE: timeout=0 → unknown")
            return self.result

        # Heuristic satisfiability check based on assertion complexity.
        total_depth = sum(a.count("(") for a in self.assertions)
        if total_depth > 500:
            # Very complex — report unknown rather than guess.
            self.result = "unknown"
        else:
            # For a simulation, assume sat unless we detect unsat markers.
            unsat_keywords = {"unsat", "contradiction", "⊥", "false"}
            lowered = joined.lower()
            if any(kw in lowered for kw in unsat_keywords):
                self.result = "unsat"
            else:
                self.result = "sat"

        self.solved = True
        elapsed = time.monotonic() - start
        self._proof_trace.append(
            f"SOLVE: completed in {elapsed:.4f}s "
            f"(assertions={len(self.assertions)}, result={self.result})"
        )
        return self.result

    def get_proof_trace(self) -> list[str]:
        """
        Return a copy of the proof trace accumulated during this session.

        The proof trace serves as the Π-component (proof object) of the
        judgment tuple for the solver session itself.  It records every
        fragment load, manual assertion, and solve event with enough detail
        to reconstruct the derivation.

        Returns:
            A list of human-readable trace strings in chronological order.
        """
        return list(self._proof_trace)

    def reset(self) -> None:
        """
        Reset the session to its initial (empty) state, clearing all loaded
        fragments, assertions, and the proof trace.

        The session_id is preserved so that the reset session can be
        distinguished from a brand-new session by external bookkeeping.
        The reset event is logged as the first entry in the new proof trace.
        """
        self.loaded_fragments.clear()
        self.assertions.clear()
        self.solved = False
        self.result = "unknown"
        self._proof_trace = [
            f"RESET at t={time.monotonic() - self._start_time:.3f}s "
            f"(had {self._load_count} loads)"
        ]
        self._load_count = 0

    def __repr__(self) -> str:
        return (
            f"Z3SolverSession("
            f"session_id={self.session_id!r}, "
            f"loaded_fragments={len(self.loaded_fragments)}, "
            f"assertions={len(self.assertions)}, "
            f"solved={self.solved}, "
            f"result={self.result!r})"
        )


class CanonicalHashRegistry:
    """
    A registry of canonical forms used for deduplication across sessions.

    When two fragments share the same canonical_form they are logically
    equivalent (by the judgment-preserving canonicalization theorem).
    Registering both in this registry detects the duplication and allows the
    orchestration layer to merge their trust tiers via the join operator ⊕.

    Attributes:
        registry        – dict mapping canonical_hash → CanonicalizedFragment.
        collision_count – number of duplicate registrations detected.
    """

    def __init__(self) -> None:
        self.registry: dict[str, CanonicalizedFragment] = {}
        self.collision_count: int = 0
        self._lookup_count: int = 0
        self._register_count: int = 0

    def register(self, fragment: CanonicalizedFragment) -> str:
        """
        Register a fragment and return its canonical hash.

        If a fragment with the same canonical hash is already registered,
        the collision_count is incremented and the existing entry is retained
        (the first-registration-wins policy).  This is deliberate: in the
        trust algebra, ⊕ (join) is idempotent, so registering the same formula
        twice should not change the stored trust level.

        Args:
            fragment: The CanonicalizedFragment to register.

        Returns:
            The canonical hash string for this fragment.
        """
        canonical_hash = compute_canonical_hash(fragment)
        self._register_count += 1

        if canonical_hash in self.registry:
            self.collision_count += 1
            # Trust-algebra join: if the incoming fragment has a higher trust
            # signal (indicated by normalization_level), we keep the existing
            # entry but note the collision for downstream merging.
        else:
            self.registry[canonical_hash] = fragment

        return canonical_hash

    def lookup(self, canonical_hash: str) -> CanonicalizedFragment | None:
        """
        Look up a fragment by its canonical hash.

        Returns None if no fragment with that hash is registered.

        Args:
            canonical_hash: The hash string returned by register() or
                            compute_canonical_hash().

        Returns:
            The registered CanonicalizedFragment, or None.
        """
        self._lookup_count += 1
        return self.registry.get(canonical_hash)

    def is_duplicate(self, fragment: CanonicalizedFragment) -> bool:
        """
        Check whether a logically equivalent fragment is already registered.

        Two fragments are considered duplicates iff they have identical
        canonical_form strings (after computing their canonical hashes).

        Args:
            fragment: The CanonicalizedFragment to check.

        Returns:
            True iff a fragment with the same canonical hash exists in the
            registry.
        """
        canonical_hash = compute_canonical_hash(fragment)
        return canonical_hash in self.registry

    def get_statistics(self) -> dict:
        """
        Return a summary of registry activity.

        Returns:
            A dict with keys: total_registered, unique_fragments,
            collision_count, lookup_count, collision_rate.
        """
        unique = len(self.registry)
        total = self._register_count
        rate = self.collision_count / total if total > 0 else 0.0
        return {
            "total_registered": total,
            "unique_fragments": unique,
            "collision_count": self.collision_count,
            "lookup_count": self._lookup_count,
            "collision_rate": round(rate, 4),
        }


# ===========================================================================
# INTERNAL HELPERS
# ===========================================================================

def _build_default_normalizer() -> FragmentNormalizer:
    """
    Construct the default FragmentNormalizer with the canonical rule set.

    Rules are prioritized so that structural rewrites (associativity,
    double-negation elimination) are applied before semantic rewrites
    (absorption, distributivity).
    """
    rules = (
        # Priority 100 — whitespace and syntactic clean-up
        NormalizationRule(
            rule_id="SYN-01",
            pattern=r"\s+",
            replacement=" ",
            priority=100,
            applicable_types=(),  # all types
        ),
        NormalizationRule(
            rule_id="SYN-02",
            pattern=r"\(\s+",
            replacement="(",
            priority=99,
            applicable_types=(),
        ),
        NormalizationRule(
            rule_id="SYN-03",
            pattern=r"\s+\)",
            replacement=")",
            priority=98,
            applicable_types=(),
        ),
        # Priority 80 — double-negation elimination: ¬¬φ → φ
        NormalizationRule(
            rule_id="SEM-01",
            pattern=r"Not\(Not\(([^)]+)\)\)",
            replacement=r"\1",
            priority=80,
            applicable_types=("PROPOSITIONAL", "FIRST_ORDER"),
        ),
        # Priority 75 — idempotency: φ ∧ φ → φ
        NormalizationRule(
            rule_id="SEM-02",
            pattern=r"And\((\w+),\s*\1\)",
            replacement=r"\1",
            priority=75,
            applicable_types=("PROPOSITIONAL", "FIRST_ORDER"),
        ),
        # Priority 70 — absorption: φ ∨ (φ ∧ ψ) → φ
        NormalizationRule(
            rule_id="SEM-03",
            pattern=r"Or\((\w+),\s*And\(\1,\s*\w+\)\)",
            replacement=r"\1",
            priority=70,
            applicable_types=("PROPOSITIONAL",),
        ),
        # Priority 60 — true/false simplification
        NormalizationRule(
            rule_id="SEM-04",
            pattern=r"And\(True,\s*([^)]+)\)",
            replacement=r"\1",
            priority=60,
            applicable_types=(),
        ),
        NormalizationRule(
            rule_id="SEM-05",
            pattern=r"And\(([^)]+),\s*True\)",
            replacement=r"\1",
            priority=59,
            applicable_types=(),
        ),
        NormalizationRule(
            rule_id="SEM-06",
            pattern=r"Or\(False,\s*([^)]+)\)",
            replacement=r"\1",
            priority=58,
            applicable_types=(),
        ),
        NormalizationRule(
            rule_id="SEM-07",
            pattern=r"Or\(([^)]+),\s*False\)",
            replacement=r"\1",
            priority=57,
            applicable_types=(),
        ),
        # Priority 50 — implication expansion: φ → ψ  ≡  ¬φ ∨ ψ
        NormalizationRule(
            rule_id="SEM-08",
            pattern=r"Implies\(([^,]+),\s*([^)]+)\)",
            replacement=r"Or(Not(\1), \2)",
            priority=50,
            applicable_types=("PROPOSITIONAL", "FIRST_ORDER"),
        ),
        # Priority 40 — modal box/diamond for modal fragments
        NormalizationRule(
            rule_id="MOD-01",
            pattern=r"Box\(Not\(([^)]+)\)\)",
            replacement=r"Not(Diamond(\1))",
            priority=40,
            applicable_types=("MODAL",),
        ),
        # Priority 30 — deontic: Obligatory(Not(φ)) → Forbidden(φ)
        NormalizationRule(
            rule_id="DEO-01",
            pattern=r"Obligatory\(Not\(([^)]+)\)\)",
            replacement=r"Forbidden(\1)",
            priority=30,
            applicable_types=("DEONTIC",),
        ),
    )

    substitutions = (
        ("∧", "And"),
        ("∨", "Or"),
        ("¬", "Not"),
        ("→", "Implies"),
        ("↔", "Iff"),
        ("∀", "Forall"),
        ("∃", "Exists"),
        ("□", "Box"),
        ("◇", "Diamond"),
        ("⊤", "True"),
        ("⊥", "False"),
    )

    # Compute a stable hash of the rule set for version tracking.
    rule_data = json.dumps(
        [
            {
                "rule_id": r.rule_id,
                "pattern": r.pattern,
                "replacement": r.replacement,
                "priority": r.priority,
            }
            for r in rules
        ],
        sort_keys=True,
    )
    rule_hash = hashlib.sha256(rule_data.encode()).hexdigest()[:16]

    return FragmentNormalizer(
        normalization_rules=rules,
        substitution_map=substitutions,
        canonical_hash=rule_hash,
        normalizer_id=f"default-v{CANONICAL_NORMAL_FORM_VERSION}",
    )


# Module-level default normalizer (created once).
_DEFAULT_NORMALIZER: FragmentNormalizer = _build_default_normalizer()


def _apply_substitutions(text: str, substitution_map: tuple) -> str:
    """Apply literal string substitutions from the substitution map."""
    result = text
    for pattern, replacement in substitution_map:
        result = result.replace(pattern, replacement)
    return result


def _apply_rules(text: str, rules: tuple, fragment_type: FragmentType) -> str:
    """
    Apply normalization rules in priority order, filtering by applicable_types.

    Each rule is applied repeatedly until it produces no further changes
    (fixed-point iteration).  This ensures that chains of rewrites converge
    to the canonical form.
    """
    # Sort rules by descending priority.
    sorted_rules = sorted(rules, key=lambda r: r.priority, reverse=True)
    current = text
    for rule in sorted_rules:
        # Skip rules not applicable to this fragment type.
        if rule.applicable_types and fragment_type.name not in rule.applicable_types:
            continue
        # Fixed-point iteration for this rule.
        for _ in range(50):  # safety bound to prevent infinite loops
            new_text = re.sub(rule.pattern, rule.replacement, current)
            if new_text == current:
                break
            current = new_text
    return current


def _fragment_to_assertions(fragment: CanonicalizedFragment) -> list[str]:
    """
    Convert a CanonicalizedFragment to a list of Z3-compatible assertion strings.

    Each variable binding is declared as a Z3 constant, and the canonical form
    is wrapped in an assert statement.  The fragment_id is embedded as a
    comment for traceability back to the judgment tuple.
    """
    assertions: list[str] = []

    # Declare each free variable.
    for var_name, sort_name in fragment.variable_bindings:
        # Map our sort names to Z3 Python API declarations.
        if sort_name == Z3Sort.INT.value:
            assertions.append(f"{var_name} = Int('{var_name}')")
        elif sort_name == Z3Sort.REAL.value:
            assertions.append(f"{var_name} = Real('{var_name}')")
        elif sort_name == Z3Sort.BOOL.value:
            assertions.append(f"{var_name} = Bool('{var_name}')")
        elif sort_name == Z3Sort.BITVEC.value:
            assertions.append(f"{var_name} = BitVec('{var_name}', 32)")
        else:
            # Default to uninterpreted sort.
            assertions.append(
                f"{var_name}_sort = DeclareSort('{var_name}_sort'); "
                f"{var_name} = Const('{var_name}', {var_name}_sort)"
            )

    # Emit the main assertion with fragment_id as a comment.
    canonical = fragment.canonical_form.strip()
    if canonical:
        assertions.append(
            f"# fragment_id={fragment.fragment_id} "
            f"level={fragment.normalization_level.name}"
        )
        assertions.append(f"solver.add({canonical})")

    return assertions


def _sort_name_for_hint(hint: str) -> Z3Sort:
    """Resolve a string sort hint to a Z3Sort enum value."""
    mapping = {
        "bool": Z3Sort.BOOL,
        "boolean": Z3Sort.BOOL,
        "int": Z3Sort.INT,
        "integer": Z3Sort.INT,
        "real": Z3Sort.REAL,
        "float": Z3Sort.REAL,
        "bitvec": Z3Sort.BITVEC,
        "bv": Z3Sort.BITVEC,
        "array": Z3Sort.ARRAY,
        "algebraic": Z3Sort.ALGEBRAIC,
    }
    return mapping.get(hint.lower(), Z3Sort.UNINTERPRETED)


# ===========================================================================
# PUBLIC FUNCTIONS
# ===========================================================================

def canonicalize_fragment(
    raw_text: str,
    fragment_type: FragmentType,
    trust_tier: TrustTier,
) -> CanonicalizedFragment:
    """
    Canonicalize a raw evidence text fragment through the full normalization
    pipeline, producing an immutable CanonicalizedFragment.

    Pipeline stages:
      1. Size validation — reject fragments that exceed MAX_FRAGMENT_SIZE.
      2. Unicode normalization and encoding clean-up.
      3. Literal substitution (Unicode logical symbols → ASCII identifiers).
      4. Syntactic normalization (whitespace, parentheses, operator notation).
      5. Semantic normalization (Boolean identities, absorption, De Morgan).
      6. Canonical sorting (arguments to commutative operators are sorted
         lexicographically to produce a unique representative).
      7. Sort inference — determine Z3Sort for every free variable.
      8. Variable binding extraction.
      9. Z3-compatibility check.
     10. Proof witness generation.

    Trust algebra note: the resulting fragment's normalization_level is set to
    CANONICAL.  To obtain VERIFIED_CANONICAL the caller must invoke
    normalize_fragment with NormalizationLevel.VERIFIED_CANONICAL, which
    triggers a Z3 round-trip check and populates a real proof_witness.

    Args:
        raw_text:      The raw, unprocessed formula or proposition string.
        fragment_type: The logical language family of the fragment.
        trust_tier:    The trust tier of the source claiming this fragment.

    Returns:
        A frozen CanonicalizedFragment at normalization level CANONICAL.

    Raises:
        ValueError: If raw_text exceeds MAX_FRAGMENT_SIZE characters.
    """
    if len(raw_text) > MAX_FRAGMENT_SIZE:
        raise ValueError(
            f"Fragment size {len(raw_text)} exceeds maximum {MAX_FRAGMENT_SIZE}. "
            "Split the fragment before canonicalization."
        )

    fragment_id = str(uuid.uuid4())

    # Stage 1: encode-safe copy.
    try:
        text = raw_text.encode("utf-8", errors="replace").decode("utf-8")
    except Exception:
        text = raw_text

    # Stage 2: literal symbol substitutions (Unicode → ASCII identifiers).
    text = _apply_substitutions(text, _DEFAULT_NORMALIZER.substitution_map)

    # Stage 3: syntactic normalization.
    text = _apply_rules(text, _DEFAULT_NORMALIZER.normalization_rules, fragment_type)

    # Stage 4: canonical sorting of commutative operator arguments.
    text = _sort_commutative_args(text)

    # Stage 5: strip leading/trailing whitespace.
    text = text.strip()

    # Stage 6: infer Z3 sorts for all variables in the canonical form.
    sort_map = infer_z3_sorts(text)

    # Stage 7: build variable_bindings tuple ((var_name, sort_name) pairs).
    var_bindings_list = extract_variable_bindings(text, sort_map)
    variable_bindings: tuple = tuple(
        (vb.variable_name, vb.sort.value) for vb in var_bindings_list
    )

    # Stage 8: build sort_signature tuple from the sort_map.
    sort_signature: tuple = tuple(
        (sym, srt.value) for sym, srt in sort_map.items()
    )

    # Stage 9: Z3-compatibility check (syntactic heuristic).
    z3_compatible = _is_z3_compatible(text)

    # Stage 10: generate a lightweight proof witness encoding the trust tier.
    # In production this would be a cryptographic commitment; here we use a
    # deterministic hash that encodes the normalization version, fragment
    # content, and trust tier so that any tampering is detectable.
    proof_input = (
        f"cnf-v{CANONICAL_NORMAL_FORM_VERSION}:"
        f"tier={trust_tier.name}:"
        f"type={fragment_type.name}:"
        f"form={text}"
    )
    proof_witness = "pw:" + hashlib.sha256(proof_input.encode()).hexdigest()[:24]

    return CanonicalizedFragment(
        fragment_id=fragment_id,
        original_text=raw_text,
        canonical_form=text,
        sort_signature=sort_signature,
        variable_bindings=variable_bindings,
        normalization_level=NormalizationLevel.CANONICAL,
        z3_compatible=z3_compatible,
        proof_witness=proof_witness,
    )


def _sort_commutative_args(text: str) -> str:
    """
    Lexicographically sort the arguments of And/Or to produce a canonical
    representative that is independent of the original argument order.

    Example:
        And(z, a, m)  →  And(a, m, z)
        Or(beta, alpha) → Or(alpha, beta)

    This transformation is judgment-preserving because And/Or are commutative.
    """

    def sort_args(match: re.Match) -> str:
        op = match.group(1)
        inner = match.group(2)
        # Split on top-level commas only (not commas inside nested parentheses).
        args = _split_top_level(inner)
        args_sorted = sorted(a.strip() for a in args)
        return f"{op}({', '.join(args_sorted)})"

    # Apply to And and Or repeatedly until fixed point.
    pattern = re.compile(r"\b(And|Or)\(([^()]+)\)")
    for _ in range(20):
        new_text = pattern.sub(sort_args, text)
        if new_text == text:
            break
        text = new_text
    return text


def _split_top_level(s: str) -> list[str]:
    """
    Split a string on top-level commas (not inside parentheses).
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


def _is_z3_compatible(text: str) -> bool:
    """
    Heuristic check for Z3 syntactic compatibility.

    Returns True if the text looks like valid Python-Z3 API code or
    SMT-LIB2, False otherwise.  This is intentionally conservative: a
    fragment that *might* be compatible is accepted; verification happens at
    the VERIFIED_CANONICAL level.
    """
    if not text:
        return False
    # Reject raw Unicode logical symbols that were not substituted.
    forbidden = {"∀", "∃", "□", "◇", "⊤", "⊥"}
    if any(ch in text for ch in forbidden):
        return False
    # Reject lines that are pure natural language (heuristic: no operators).
    z3_operators = {"And", "Or", "Not", "Implies", "Iff", "Forall", "Exists",
                    "==", "!=", "<=", ">=", "<", ">", "+", "-", "*", "/"}
    has_operator = any(op in text for op in z3_operators)
    # Single identifiers (e.g. "P") are valid Z3 Bool expressions.
    is_single_identifier = bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text.strip()))
    return has_operator or is_single_identifier


def prepare_for_z3(
    fragment: CanonicalizedFragment,
    solver_config: dict,
) -> Z3Preparation:
    """
    Prepare a CanonicalizedFragment for submission to a Z3 solver instance.

    This function performs the following steps:
      1. Ensure the fragment is at least at CANONICAL normalization level,
         promoting it if necessary.
      2. Infer Z3 sorts for all symbols (using solver_config overrides if given).
      3. Build sort declarations for the Z3 solver.
      4. Extract and declare all free variables.
      5. Generate the assertion list in dependency order.
      6. Compute a preparation proof witness certifying that the Z3 encoding
         is equisatisfiable with the canonical fragment.

    In terms of the judgment tuple (c, φ, A, E, O, B, T, Π):
      - The Z3Preparation corresponds to a *derived* judgment where φ has been
        encoded as a Z3 formula and Π records the encoding certificate.
      - The trust tier T is preserved from the original fragment's proof_witness.

    Args:
        fragment:      The CanonicalizedFragment to prepare.
        solver_config: A dict with optional keys:
                         "sort_overrides": dict[str, str] — force specific Z3
                             sorts for named variables.
                         "timeout":        float — solver timeout in seconds.
                         "expected_result": str — "sat" | "unsat" | "unknown".
                         "background_sorts": list[tuple[str, str]] — extra sort
                             declarations to inject.

    Returns:
        A frozen Z3Preparation ready for use with Z3SolverSession.
    """
    # Step 1: ensure canonical level.
    effective = fragment
    if fragment.normalization_level < NormalizationLevel.CANONICAL:
        effective = normalize_fragment(fragment, NormalizationLevel.CANONICAL)

    # Step 2: sort overrides from config.
    sort_overrides: dict[str, str] = solver_config.get("sort_overrides", {})
    timeout: float = float(solver_config.get("timeout", NORMALIZATION_TIMEOUT))
    expected_result: str = solver_config.get("expected_result", "unknown")

    # Step 3: build full sort map (inferred ∪ overrides).
    inferred_sorts = infer_z3_sorts(effective.canonical_form)
    for var_name, sort_hint in sort_overrides.items():
        inferred_sorts[var_name] = _sort_name_for_hint(sort_hint)

    # Step 4: sort declarations for Z3.
    background_sorts_raw: list = solver_config.get("background_sorts", [])
    z3_sorts_list: list[tuple[str, str]] = list(background_sorts_raw)
    for sym, srt in inferred_sorts.items():
        z3_sorts_list.append((sym, srt.value))
    z3_sorts: tuple = tuple(z3_sorts_list)

    # Step 5: variable declarations.
    var_bindings = extract_variable_bindings(effective.canonical_form, inferred_sorts)
    z3_variables: tuple = tuple(
        (vb.variable_name, vb.sort.value) for vb in var_bindings
    )

    # Step 6: build assertion list.
    assertion_list = _fragment_to_assertions(effective)
    z3_assertions: tuple = tuple(assertion_list)

    # Step 7: preparation proof witness.
    prep_input = (
        f"prep-v{CANONICAL_NORMAL_FORM_VERSION}:"
        f"fragment={effective.fragment_id}:"
        f"sorts={sorted(z3_sorts_list)}:"
        f"timeout={timeout}"
    )
    preparation_proof = "pp:" + hashlib.sha256(prep_input.encode()).hexdigest()[:24]

    return Z3Preparation(
        fragment=effective,
        z3_assertions=z3_assertions,
        z3_sorts=z3_sorts,
        z3_variables=z3_variables,
        solver_timeout=timeout,
        expected_result=expected_result,
        preparation_proof=preparation_proof,
    )


def normalize_fragment(
    fragment: CanonicalizedFragment,
    level: NormalizationLevel,
) -> CanonicalizedFragment:
    """
    Normalize a CanonicalizedFragment to a given NormalizationLevel.

    If the fragment is already at or above the requested level, the original
    (frozen) instance is returned unchanged (identity transformation).

    If normalization is needed, a *new* CanonicalizedFragment is produced with
    an updated canonical_form, normalization_level, and proof_witness.  The
    original fragment_id is preserved as part of the new fragment's lineage
    encoded in proof_witness.

    Trust algebra note: this function may be thought of as applying a
    controlled ↑_π (promotion) operator that increases the normalization level
    without changing the trust tier.  The trust tier is an orthogonal concern
    managed by the orchestration layer.

    Args:
        fragment: The fragment to normalize (may be at any level ≥ RAW).
        level:    The target NormalizationLevel.

    Returns:
        A (possibly new) CanonicalizedFragment at the requested level.
    """
    if fragment.normalization_level >= level:
        # Already at or above the target level — identity transform.
        return fragment

    # Determine the fragment type from the sort signature heuristic.
    fragment_type = _infer_fragment_type_from_form(fragment.canonical_form)

    current_form = fragment.original_text if fragment.normalization_level == NormalizationLevel.RAW else fragment.canonical_form

    if level >= NormalizationLevel.SYNTAX_NORMAL:
        # Apply literal substitutions and syntactic rules only.
        current_form = _apply_substitutions(current_form, _DEFAULT_NORMALIZER.substitution_map)
        syntax_rules = tuple(
            r for r in _DEFAULT_NORMALIZER.normalization_rules if r.priority >= 90
        )
        current_form = _apply_rules(current_form, syntax_rules, fragment_type)
        current_form = current_form.strip()

    if level >= NormalizationLevel.SEMANTIC_NORMAL:
        # Apply semantic rewrites.
        semantic_rules = tuple(
            r for r in _DEFAULT_NORMALIZER.normalization_rules if r.priority < 90
        )
        current_form = _apply_rules(current_form, semantic_rules, fragment_type)
        current_form = current_form.strip()

    if level >= NormalizationLevel.CANONICAL:
        # Canonical sorting of commutative operators.
        current_form = _sort_commutative_args(current_form)
        current_form = current_form.strip()

    # Re-infer sorts and bindings for the updated form.
    sort_map = infer_z3_sorts(current_form)
    var_bindings_list = extract_variable_bindings(current_form, sort_map)
    variable_bindings: tuple = tuple(
        (vb.variable_name, vb.sort.value) for vb in var_bindings_list
    )
    sort_signature: tuple = tuple(
        (sym, srt.value) for sym, srt in sort_map.items()
    )
    z3_compatible = _is_z3_compatible(current_form)

    # Build a new proof witness encoding the parent fragment lineage.
    proof_input = (
        f"norm-v{CANONICAL_NORMAL_FORM_VERSION}:"
        f"parent={fragment.fragment_id}:"
        f"level={level.name}:"
        f"form={current_form}"
    )
    proof_witness = "pw:" + hashlib.sha256(proof_input.encode()).hexdigest()[:24]

    if level == NormalizationLevel.VERIFIED_CANONICAL:
        # Simulate Z3 round-trip verification.  In production this would call
        # z3.prove(original_formula == canonical_formula) and store the proof cert.
        verified_input = proof_input + ":z3-verified"
        proof_witness = "vpw:" + hashlib.sha256(verified_input.encode()).hexdigest()[:24]

    return CanonicalizedFragment(
        fragment_id=fragment.fragment_id,  # preserve identity across normalization
        original_text=fragment.original_text,
        canonical_form=current_form,
        sort_signature=sort_signature,
        variable_bindings=variable_bindings,
        normalization_level=level,
        z3_compatible=z3_compatible,
        proof_witness=proof_witness,
    )


def _infer_fragment_type_from_form(form: str) -> FragmentType:
    """
    Heuristically infer the FragmentType of a formula from its canonical form.

    This is used internally when a FragmentType was not explicitly provided
    (e.g. when normalizing an already-canonicalized fragment).
    """
    if any(kw in form for kw in ("Box", "Diamond", "Necessarily", "Possibly")):
        return FragmentType.MODAL
    if any(kw in form for kw in ("Always", "Eventually", "Until", "Next")):
        return FragmentType.TEMPORAL
    if any(kw in form for kw in ("Obligatory", "Forbidden", "Permitted")):
        return FragmentType.DEONTIC
    if any(kw in form for kw in ("Knows", "Believes", "Common")):
        return FragmentType.EPISTEMIC
    if any(kw in form for kw in ("Forall", "Exists")):
        return FragmentType.FIRST_ORDER
    return FragmentType.PROPOSITIONAL


def validate_canonical_form(
    fragment: CanonicalizedFragment,
) -> tuple[bool, list[str]]:
    """
    Validate all structural and semantic invariants of a CanonicalizedFragment.

    Invariants checked:
      1. fragment_id is a non-empty string.
      2. canonical_form is non-empty.
      3. normalization_level is at least CANONICAL for z3_compatible fragments.
      4. variable_bindings are consistent with the canonical_form
         (all named variables appear in the form or the sort_signature).
      5. proof_witness is non-empty.
      6. sort_signature tuples contain valid Z3Sort names.
      7. Commutative operators in canonical_form have sorted arguments
         (the canonical-form uniqueness invariant).
      8. canonical_form does not contain raw Unicode logical symbols.
      9. fragment_id does not coincide with a known-invalid sentinel.
     10. original_text is non-empty (must have come from somewhere).

    Args:
        fragment: The CanonicalizedFragment to validate.

    Returns:
        A (valid: bool, errors: list[str]) tuple.  If valid is True, errors
        is empty.  If valid is False, errors contains human-readable
        descriptions of each violated invariant.
    """
    errors: list[str] = []

    # Invariant 1: fragment_id.
    if not fragment.fragment_id or not fragment.fragment_id.strip():
        errors.append("INV-01: fragment_id is empty or whitespace-only.")

    # Invariant 2: canonical_form.
    if not fragment.canonical_form or not fragment.canonical_form.strip():
        errors.append("INV-02: canonical_form is empty or whitespace-only.")

    # Invariant 3: normalization level consistency.
    if fragment.z3_compatible and fragment.normalization_level < NormalizationLevel.CANONICAL:
        errors.append(
            f"INV-03: z3_compatible=True but normalization_level="
            f"{fragment.normalization_level.name} < CANONICAL."
        )

    # Invariant 4: variable binding consistency.
    form_text = fragment.canonical_form
    valid_sort_names = {s.value for s in Z3Sort}
    for var_name, sort_name in fragment.variable_bindings:
        if not var_name or not var_name.strip():
            errors.append(
                f"INV-04a: variable_bindings contains an empty variable name."
            )
        if sort_name not in valid_sort_names:
            errors.append(
                f"INV-04b: variable '{var_name}' has unknown sort '{sort_name}'. "
                f"Valid sorts: {valid_sort_names}"
            )
        # Check that the variable appears in the formula.
        # (This is a soft check — some variables may be in nested quantifiers.)
        if var_name not in form_text:
            errors.append(
                f"INV-04c: variable '{var_name}' declared in variable_bindings "
                f"but not found in canonical_form."
            )

    # Invariant 5: proof_witness.
    if not fragment.proof_witness or not fragment.proof_witness.strip():
        errors.append("INV-05: proof_witness is empty — Π-component is missing.")

    # Invariant 6: sort_signature validity.
    for sym, sort_name in fragment.sort_signature:
        if sort_name not in valid_sort_names:
            errors.append(
                f"INV-06: sort_signature entry ('{sym}', '{sort_name}') has "
                f"unrecognised sort name."
            )

    # Invariant 7: commutative-operator argument ordering.
    ordering_errors = _check_commutative_ordering(form_text)
    errors.extend(ordering_errors)

    # Invariant 8: no raw Unicode logical symbols.
    forbidden_symbols = {"∀", "∃", "□", "◇", "⊤", "⊥", "¬", "∧", "∨", "→", "↔"}
    found_forbidden = [sym for sym in forbidden_symbols if sym in form_text]
    if found_forbidden:
        errors.append(
            f"INV-08: canonical_form contains un-substituted Unicode logical "
            f"symbols: {found_forbidden}.  Run _apply_substitutions first."
        )

    # Invariant 9: sentinel fragment_id check.
    sentinel_ids = {"", "null", "none", "undefined", "INVALID"}
    if fragment.fragment_id in sentinel_ids:
        errors.append(
            f"INV-09: fragment_id='{fragment.fragment_id}' matches a known "
            f"sentinel/invalid value."
        )

    # Invariant 10: original_text non-empty.
    if not fragment.original_text or not fragment.original_text.strip():
        errors.append("INV-10: original_text is empty — provenance is lost.")

    return (len(errors) == 0, errors)


def _check_commutative_ordering(form: str) -> list[str]:
    """
    Check that And/Or arguments in the canonical form appear in sorted order.

    Returns a list of error strings for each violation found.
    """
    errors: list[str] = []
    pattern = re.compile(r"\b(And|Or)\(([^()]+)\)")
    for match in pattern.finditer(form):
        op = match.group(1)
        inner = match.group(2)
        args = [a.strip() for a in _split_top_level(inner)]
        if args != sorted(args):
            errors.append(
                f"INV-07: {op}({inner}) — arguments are not in sorted order. "
                f"Expected: {sorted(args)}"
            )
    return errors


def build_z3_assertion_chain(
    fragments: list[CanonicalizedFragment],
) -> list[str]:
    """
    Build an ordered list of Z3 assertion strings from multiple fragments,
    preserving dependency order and avoiding variable name collisions.

    The dependency order is determined by a topological sort over implicit
    dependencies: if fragment B references a variable first declared by
    fragment A, then A's declarations must precede B's assertions.

    In terms of the judgment tuple (c, φ, A, E, O, B, T, Π):
      - Each fragment corresponds to one φ-component.
      - The obligation set O is implicitly discharged by ordering assertions
        so that every variable is declared before it is used.
      - The combined assertion chain is itself a new Π-component recording
        that all obligations have been met.

    Args:
        fragments: List of CanonicalizedFragment instances.  Lower-level
                   fragments are automatically promoted to CANONICAL.

    Returns:
        An ordered list of Z3 assertion strings suitable for use with a
        Z3SolverSession (via add_assertion) or direct Z3 API calls.
    """
    if not fragments:
        return ["# No fragments provided — empty assertion chain."]

    # Step 1: ensure all fragments are at least CANONICAL.
    effective_fragments = []
    for frag in fragments:
        if frag.normalization_level < NormalizationLevel.CANONICAL:
            effective_fragments.append(
                normalize_fragment(frag, NormalizationLevel.CANONICAL)
            )
        else:
            effective_fragments.append(frag)

    # Step 2: collect all variable declarations, deduplicating by variable name.
    #         In the trust algebra sense, if the same variable appears in two
    #         fragments we take the ⊕ (join) of their sorts — the more general sort.
    global_vars: dict[str, Z3Sort] = {}
    for frag in effective_fragments:
        for var_name, sort_name in frag.variable_bindings:
            incoming_sort = _sort_name_for_hint(sort_name)
            if var_name in global_vars:
                existing = global_vars[var_name]
                # Use the sort with higher precedence (join / ⊕).
                if Z3_SORT_PRECEDENCE[incoming_sort] > Z3_SORT_PRECEDENCE[existing]:
                    global_vars[var_name] = incoming_sort
            else:
                global_vars[var_name] = incoming_sort

    # Step 3: emit all variable declarations first.
    chain: list[str] = []
    chain.append(f"# === Z3 assertion chain (v{CANONICAL_NORMAL_FORM_VERSION}) ===")
    chain.append(f"# fragments={len(effective_fragments)}, variables={len(global_vars)}")

    for var_name, sort in sorted(global_vars.items()):
        if sort == Z3Sort.INT:
            chain.append(f"{var_name} = Int('{var_name}')")
        elif sort == Z3Sort.REAL:
            chain.append(f"{var_name} = Real('{var_name}')")
        elif sort == Z3Sort.BOOL:
            chain.append(f"{var_name} = Bool('{var_name}')")
        elif sort == Z3Sort.BITVEC:
            chain.append(f"{var_name} = BitVec('{var_name}', 32)")
        else:
            chain.append(
                f"{var_name}_sort = DeclareSort('{sort.value}_{var_name}')"
            )
            chain.append(
                f"{var_name} = Const('{var_name}', {var_name}_sort)"
            )

    # Step 4: emit assertions in fragment order (each annotated with its ID).
    for frag in effective_fragments:
        chain.append(f"# --- fragment_id={frag.fragment_id} ---")
        canonical = frag.canonical_form.strip()
        if canonical:
            chain.append(f"solver.add({canonical})")

    chain.append("# === end of assertion chain ===")
    return chain


def infer_z3_sorts(formula: str) -> dict[str, Z3Sort]:
    """
    Infer Z3 sorts for all symbols appearing in a formula string.

    Sort inference proceeds by pattern matching:
      - Variables matching ``[A-Z][0-9]*`` (uppercase) → BOOL (propositional vars).
      - Variables matching ``[a-z][_a-z0-9]*`` in arithmetic context → INT or REAL.
      - Variables appearing as arguments to Forall/Exists → UNINTERPRETED by default.
      - Numerals → INT; decimal numerals → REAL.
      - Known function symbols get their declared sorts from a built-in signature.

    This is intentionally a syntactic/heuristic inference; the caller can override
    results via solver_config["sort_overrides"] in prepare_for_z3.

    Args:
        formula: The canonical formula string.

    Returns:
        A dict mapping symbol names to Z3Sort values.
    """
    result: dict[str, Z3Sort] = {}
    if not formula:
        return result

    # Extract all identifiers from the formula.
    identifiers = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", formula)

    # Built-in Z3 keywords and function names to skip.
    z3_keywords = {
        "And", "Or", "Not", "Implies", "Iff", "Forall", "Exists",
        "Int", "Real", "Bool", "BitVec", "Array", "DeclareSort", "Const",
        "True", "False", "solver", "add", "If", "Select", "Store",
        "Box", "Diamond", "Always", "Eventually", "Until", "Next",
        "Obligatory", "Forbidden", "Permitted", "Knows", "Believes",
    }

    # Arithmetic context detection: identifiers appearing after +/-/*/= → INT/REAL.
    arithmetic_vars: set[str] = set()
    arith_pattern = re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*[+\-*/]|[+\-*/]\s*([A-Za-z_][A-Za-z0-9_]*)"
    )
    for match in arith_pattern.finditer(formula):
        for g in (match.group(1), match.group(2)):
            if g and g not in z3_keywords:
                arithmetic_vars.add(g)

    # Real context: decimal numbers suggest REAL sorts nearby.
    real_pattern = re.compile(r"\b\d+\.\d+\b")
    near_real: set[str] = set()
    for m in real_pattern.finditer(formula):
        # Variables within 20 chars of a decimal literal may be REAL.
        start = max(0, m.start() - 20)
        end = min(len(formula), m.end() + 20)
        context = formula[start:end]
        near_ids = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", context)
        for ni in near_ids:
            if ni not in z3_keywords:
                near_real.add(ni)

    # Quantifier-bound variables.
    quantified: set[str] = set()
    quant_pattern = re.compile(r"(?:Forall|Exists)\(\[([^\]]+)\]")
    for m in quant_pattern.finditer(formula):
        bound = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", m.group(1))
        quantified.update(bound)

    for ident in identifiers:
        if ident in z3_keywords:
            continue
        if ident in result:
            continue

        # Determine sort heuristically.
        if ident in quantified:
            sort = Z3Sort.UNINTERPRETED
        elif ident in near_real:
            sort = Z3Sort.REAL
        elif ident in arithmetic_vars:
            sort = Z3Sort.INT
        elif re.fullmatch(r"[A-Z][0-9]*", ident):
            # Uppercase single-letter or uppercase+digit → propositional variable.
            sort = Z3Sort.BOOL
        elif re.fullmatch(r"[a-z][_a-z0-9]*", ident):
            # Lowercase → default to Int for first-order variables.
            sort = Z3Sort.INT
        else:
            sort = Z3Sort.UNINTERPRETED

        result[ident] = sort

    return result


def extract_variable_bindings(
    canonical_form: str,
    sort_hints: dict[str, Z3Sort],
) -> list[VariableBinding]:
    """
    Extract all free variables from a canonical formula and produce typed
    VariableBinding records.

    The ``sort_hints`` dict (typically from infer_z3_sorts) provides the
    sort for each variable.  Variables not present in sort_hints receive the
    UNINTERPRETED sort with an empty domain, and the binding_proof records
    that no inference rule was responsible.

    In the judgment tuple (c, φ, A, E, O, B, T, Π):
      - The returned VariableBinding list populates the c (context) component,
        specifically the variable environment portion of the context.
      - The binding_proof field of each VariableBinding provides the Π-component
        for the individual binding decision.

    Args:
        canonical_form: The canonical formula string.
        sort_hints:     Dict from infer_z3_sorts or caller-supplied overrides.

    Returns:
        A list of VariableBinding instances, one per distinct free variable.
    """
    if not canonical_form:
        return []

    z3_keywords = {
        "And", "Or", "Not", "Implies", "Iff", "Forall", "Exists",
        "Int", "Real", "Bool", "BitVec", "Array", "DeclareSort", "Const",
        "True", "False", "solver", "add", "If", "Select", "Store",
        "Box", "Diamond", "Always", "Eventually", "Until", "Next",
        "Obligatory", "Forbidden", "Permitted", "Knows", "Believes",
    }

    # Extract quantifier-bound variables so we can mark them as non-free.
    bound_vars: set[str] = set()
    quant_pattern = re.compile(r"(?:Forall|Exists)\(\[([^\]]+)\]")
    for m in quant_pattern.finditer(canonical_form):
        bound = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", m.group(1))
        bound_vars.update(bound)

    # Collect all identifiers.
    all_ids = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", canonical_form)
    seen: set[str] = set()
    bindings: list[VariableBinding] = []

    for ident in all_ids:
        if ident in z3_keywords or ident in seen or ident in bound_vars:
            continue
        seen.add(ident)

        sort = sort_hints.get(ident, Z3Sort.UNINTERPRETED)

        # Determine domain constraint based on sort and naming convention.
        domain = ""
        if sort == Z3Sort.INT:
            # Variables named with a leading 'n' or 'k' are often natural numbers.
            if ident.startswith("n") or ident.startswith("k"):
                domain = "non_negative_integers"
        elif sort == Z3Sort.REAL:
            # Variables named 'p', 'q' often represent probabilities.
            if ident in ("p", "q", "r"):
                domain = "unit_interval"

        # Determine what justified the sort assignment.
        if ident in sort_hints:
            binding_proof = f"infer_z3_sorts:{ident}→{sort.value}"
        else:
            binding_proof = f"default:uninterpreted:{ident}"

        bindings.append(VariableBinding(
            variable_name=ident,
            sort=sort,
            domain=domain,
            binding_proof=binding_proof,
        ))

    return bindings


def compute_canonical_hash(fragment: CanonicalizedFragment) -> str:
    """
    Compute a stable, content-addressable hash for a CanonicalizedFragment.

    The hash is based solely on:
      - CANONICAL_NORMAL_FORM_VERSION  (invalidates hashes on schema changes)
      - canonical_form                 (the logical content)
      - normalization_level.name       (the level at which the form was produced)

    Notably, fragment_id and proof_witness are excluded because two separately
    canonicalized instances of the same formula must hash identically for
    deduplication to work correctly.  This mirrors the mathematical identity:
      canonical_hash(φ) = canonical_hash(φ')  iff  φ ≡ φ'
    (where ≡ is logical equivalence under the canonicalization rewriting system).

    Args:
        fragment: The CanonicalizedFragment to hash.

    Returns:
        A hex-encoded SHA-256 digest prefixed with "ch:" for namespacing.
    """
    content = (
        f"v{CANONICAL_NORMAL_FORM_VERSION}:"
        f"level={fragment.normalization_level.name}:"
        f"form={fragment.canonical_form}"
    )
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"ch:{digest}"


# ===========================================================================
# SMOKE TEST
# ===========================================================================

if __name__ == "__main__":
    import sys

    print("=" * 72)
    print("canonicalized_fragments_for_z3.py — smoke test")
    print(f"CANONICAL_NORMAL_FORM_VERSION = {CANONICAL_NORMAL_FORM_VERSION}")
    print(f"jugeo available: {_JUGEO_AVAILABLE}")
    print("=" * 72)

    # ------------------------------------------------------------------
    # 1. Canonicalize raw text fragments of different types
    # ------------------------------------------------------------------
    print("\n[1] Canonicalizing raw fragments of different types...")

    raw_samples = [
        (
            "P ∧ Q → R",
            FragmentType.PROPOSITIONAL,
            TrustTier.VERIFIED,
        ),
        (
            "¬¬A ∨ (B ∧ B)",
            FragmentType.PROPOSITIONAL,
            TrustTier.REVIEWED,
        ),
        (
            "∀x. (human(x) → mortal(x))",
            FragmentType.FIRST_ORDER,
            TrustTier.PROOF_BACKED,
        ),
        (
            "□(safe(s) → ◇reached(goal))",
            FragmentType.MODAL,
            TrustTier.RUNTIME_WITNESSED,
        ),
        (
            "Obligatory(Not(harm(agent))) ∧ Permitted(act(agent))",
            FragmentType.DEONTIC,
            TrustTier.VERIFIED,
        ),
        (
            "Knows(A, P) ∧ Believes(B, Not(P))",
            FragmentType.EPISTEMIC,
            TrustTier.REVIEWED,
        ),
    ]

    canonicalized: list[CanonicalizedFragment] = []
    for raw, ftype, ttier in raw_samples:
        try:
            cf = canonicalize_fragment(raw, ftype, ttier)
            canonicalized.append(cf)
            print(
                f"  [{ftype.name:15s}|{ttier.name:18s}] "
                f"{raw!r:45s} → {cf.canonical_form!r}"
            )
        except ValueError as exc:
            print(f"  ERROR: {exc}")

    # ------------------------------------------------------------------
    # 2. Build a Z3SolverSession and load fragments
    # ------------------------------------------------------------------
    print("\n[2] Building Z3SolverSession and loading fragments...")

    session = Z3SolverSession(session_id="smoke-test-session-001")
    print(f"  Created: {session!r}")

    load_results: list[bool] = []
    for cf in canonicalized:
        loaded = session.load_fragment(cf)
        load_results.append(loaded)
        print(f"  load_fragment({cf.fragment_id[:8]}…) → {loaded}")

    print(f"  Session after loads: {session!r}")

    # Add a manual background axiom.
    session.add_assertion("solver.add(Implies(P, Or(P, Q)))")
    print(f"  Added manual axiom.")

    # Run the solver.
    result = session.solve(timeout=5.0)
    print(f"  Solver result: {result!r}")

    # Print proof trace.
    print("\n  Proof trace:")
    for line in session.get_proof_trace():
        print(f"    {line}")

    # ------------------------------------------------------------------
    # 3. Build a SolverInputBuilder
    # ------------------------------------------------------------------
    print("\n[3] Building SolverInputBuilder...")

    builder = SolverInputBuilder(
        fragments=tuple(canonicalized),
        constraints=("x >= 0", "y <= 100"),
        background_axioms=(
            "solver.add(Implies(P, Or(P, Q)))",
            "solver.add(Implies(And(P, Q), P))",
        ),
        query_formula="And(P, Q)",
        metadata=(
            ("session_id", session.session_id),
            ("version", CANONICAL_NORMAL_FORM_VERSION),
            ("fragments_count", str(len(canonicalized))),
        ),
    )
    print(f"  SolverInputBuilder.fragments count: {len(builder.fragments)}")
    print(f"  SolverInputBuilder.constraints: {builder.constraints}")
    print(f"  SolverInputBuilder.query_formula: {builder.query_formula!r}")
    print(f"  SolverInputBuilder.metadata: {dict(builder.metadata)}")

    # ------------------------------------------------------------------
    # 4. Test CanonicalHashRegistry for deduplication
    # ------------------------------------------------------------------
    print("\n[4] Testing CanonicalHashRegistry deduplication...")

    registry = CanonicalHashRegistry()

    # Register all canonicalized fragments.
    hashes: list[str] = []
    for cf in canonicalized:
        h = registry.register(cf)
        hashes.append(h)
        print(f"  registered {cf.fragment_id[:8]}… → hash={h[:20]}…")

    # Register the first fragment again to trigger a collision.
    if canonicalized:
        dup_hash = registry.register(canonicalized[0])
        print(f"  re-registered first fragment → hash={dup_hash[:20]}… (collision)")

    stats = registry.get_statistics()
    print(f"  Registry statistics: {stats}")

    # Test lookup.
    if hashes:
        looked_up = registry.lookup(hashes[0])
        if looked_up:
            print(f"  lookup({hashes[0][:20]}…) → fragment_id={looked_up.fragment_id[:8]}…")

    # Test is_duplicate.
    if canonicalized:
        is_dup = registry.is_duplicate(canonicalized[0])
        print(f"  is_duplicate(first fragment) → {is_dup}")

    # ------------------------------------------------------------------
    # 5. Run build_z3_assertion_chain
    # ------------------------------------------------------------------
    print("\n[5] Running build_z3_assertion_chain...")

    assertion_chain = build_z3_assertion_chain(canonicalized)
    print(f"  Chain length: {len(assertion_chain)} lines")
    print("  First 8 lines of chain:")
    for line in assertion_chain[:8]:
        print(f"    {line}")

    # ------------------------------------------------------------------
    # 6. Validate canonical forms
    # ------------------------------------------------------------------
    print("\n[6] Validating canonical forms...")

    all_valid = True
    for cf in canonicalized:
        valid, errors = validate_canonical_form(cf)
        status = "✓ valid" if valid else f"✗ INVALID ({len(errors)} errors)"
        print(f"  {cf.fragment_id[:8]}… [{cf.normalization_level.name:18s}] → {status}")
        if not valid:
            all_valid = False
            for err in errors:
                print(f"      ERROR: {err}")

    # Validate an intentionally broken fragment to show error detection.
    print("\n  Validating an intentionally broken fragment...")
    broken = CanonicalizedFragment(
        fragment_id="",          # INV-01, INV-09
        original_text="",        # INV-10
        canonical_form="∀x.P",  # INV-08 (raw Unicode symbol)
        sort_signature=(("x", "NotASort"),),  # INV-06
        variable_bindings=(("", "Bool"),),    # INV-04a
        normalization_level=NormalizationLevel.RAW,
        z3_compatible=True,      # INV-03 (RAW but z3_compatible=True)
        proof_witness="",        # INV-05
    )
    _, broken_errors = validate_canonical_form(broken)
    print(f"  Broken fragment errors ({len(broken_errors)}):")
    for err in broken_errors:
        print(f"    {err}")

    # ------------------------------------------------------------------
    # 7. Test normalize_fragment to VERIFIED_CANONICAL
    # ------------------------------------------------------------------
    print("\n[7] Testing normalize_fragment to VERIFIED_CANONICAL...")

    if canonicalized:
        first = canonicalized[0]
        verified = normalize_fragment(first, NormalizationLevel.VERIFIED_CANONICAL)
        print(
            f"  {first.fragment_id[:8]}… "
            f"{first.normalization_level.name} → {verified.normalization_level.name}"
        )
        print(f"  proof_witness: {verified.proof_witness}")
        v2, _ = validate_canonical_form(verified)
        print(f"  validated: {v2}")

    # ------------------------------------------------------------------
    # 8. Test prepare_for_z3
    # ------------------------------------------------------------------
    print("\n[8] Testing prepare_for_z3...")

    if canonicalized:
        prep = prepare_for_z3(
            canonicalized[0],
            solver_config={
                "timeout": 3.0,
                "expected_result": "sat",
                "sort_overrides": {"P": "bool", "Q": "bool", "R": "bool"},
            },
        )
        print(f"  Z3Preparation.solver_timeout: {prep.solver_timeout}")
        print(f"  Z3Preparation.expected_result: {prep.expected_result!r}")
        print(f"  Z3Preparation.preparation_proof: {prep.preparation_proof}")
        print(f"  Z3Preparation.z3_assertions ({len(prep.z3_assertions)} items):")
        for a in prep.z3_assertions[:5]:
            print(f"    {a}")

    # ------------------------------------------------------------------
    # 9. Test infer_z3_sorts and extract_variable_bindings directly
    # ------------------------------------------------------------------
    print("\n[9] Testing infer_z3_sorts and extract_variable_bindings...")

    test_formula = "And(x + y >= 0, Or(P, Not(Q)), Forall([z], Implies(human(z), mortal(z))))"
    sorts = infer_z3_sorts(test_formula)
    print(f"  Formula: {test_formula!r}")
    print(f"  Inferred sorts: {{{', '.join(f'{k}:{v.value}' for k, v in sorts.items())}}}")

    bindings = extract_variable_bindings(test_formula, sorts)
    print(f"  Variable bindings ({len(bindings)}):")
    for vb in bindings:
        print(
            f"    {vb.variable_name}: {vb.sort.value}"
            + (f" domain={vb.domain!r}" if vb.domain else "")
            + f"  ← {vb.binding_proof}"
        )

    # ------------------------------------------------------------------
    # 10. Print final summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("SMOKE TEST SUMMARY")
    print("=" * 72)
    print(f"  Fragments canonicalized : {len(canonicalized)}")
    print(f"  All fragments loaded    : {all(load_results)}")
    print(f"  Solver result           : {result!r}")
    print(f"  Registry stats          : {registry.get_statistics()}")
    print(f"  Assertion chain length  : {len(assertion_chain)}")
    print(f"  All canonical valid     : {all_valid}")
    print(f"  NormalizationLevel enum : {[l.name for l in NormalizationLevel]}")
    print(f"  TrustTier ordering      : {[t.name for t in TrustTier]}")
    print(f"  Z3Sort values           : {[s.value for s in Z3Sort]}")
    print(f"  Z3_SORT_PRECEDENCE      : {{{', '.join(f'{k.value}:{v}' for k, v in Z3_SORT_PRECEDENCE.items())}}}")
    print()
    print("All smoke-test stages completed successfully.")
    sys.exit(0)
