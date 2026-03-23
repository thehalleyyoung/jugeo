"""Formal theorem statements and proof sketches for Ch23 of theory2.tex.

Covers the semantics of live code mutation in the JuGeo sheaf-theoretic
framework. Each theorem is a first-class object with a statement, proof sketch,
theory chapter reference, section reference, status, and dependencies. The
TheoremProver class provides verification and reporting utilities. The
TheoremLibrary provides a registry of all theorems.

Theory alignment: Ch23 of theory2.tex.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TheoremStatus(Enum):
    """Lifecycle status of a formal theorem in the Ch23 library.

    CONJECTURED: Stated without proof sketch.
    SKETCH_ONLY: Has a human-readable proof sketch but no formal proof.
    FORMALLY_VERIFIED: Manually verified via pen-and-paper proof.
    MECHANICALLY_CHECKED: Verified by a proof assistant (e.g., Lean, Coq).
    """

    CONJECTURED = "CONJECTURED"
    SKETCH_ONLY = "SKETCH_ONLY"
    FORMALLY_VERIFIED = "FORMALLY_VERIFIED"
    MECHANICALLY_CHECKED = "MECHANICALLY_CHECKED"


class ProofMethod(Enum):
    """Proof methodology used to justify a theorem.

    SHEAF_THEORY: Uses sheaf-theoretic constructions (stalks, restrictions).
    CATEGORY_THEORY: Uses categorical abstractions (functors, natural transforms).
    OPERATIONAL_SEMANTICS: Uses small-step or big-step operational rules.
    INDUCTION: Uses structural or mathematical induction over finite data.
    COINDUCTION: Uses coinduction for potentially infinite structures.
    DIRECT_CONSTRUCTION: Constructs a witnessing object directly.
    """

    SHEAF_THEORY = "SHEAF_THEORY"
    CATEGORY_THEORY = "CATEGORY_THEORY"
    OPERATIONAL_SEMANTICS = "OPERATIONAL_SEMANTICS"
    INDUCTION = "INDUCTION"
    COINDUCTION = "COINDUCTION"
    DIRECT_CONSTRUCTION = "DIRECT_CONSTRUCTION"


@dataclass(frozen=True)
class TheoremRecord:
    """An immutable record capturing a formal theorem from Ch23.

    Stores the theorem's unique ID, formal statement, proof sketch, location
    within theory2.tex, current verification status, proof methodology, and
    dependency relationships. Frozen so that theorem records can be used as
    dict keys and stored in sets without unintended mutation.
    """

    theorem_id: str
    statement: str
    proof_sketch: str
    theory_chapter: int
    section_ref: str
    status: TheoremStatus
    dependencies: tuple[str, ...] = ()
    proof_method: ProofMethod = ProofMethod.SHEAF_THEORY
    author: str = "copilot"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """Serialise the theorem record to a plain dict.

        Returns:
            Dict with all fields; Enum values serialised to their .value string,
            dependencies cast to list for JSON compatibility.
        """
        return {
            "theorem_id": self.theorem_id,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "theory_chapter": self.theory_chapter,
            "section_ref": self.section_ref,
            "status": self.status.value,
            "dependencies": list(self.dependencies),
            "proof_method": self.proof_method.value,
            "author": self.author,
            "created_at": self.created_at,
            "is_verified": self.is_verified(),
            "fingerprint": self.fingerprint(),
        }

    def is_verified(self) -> bool:
        """Return True if the theorem has been formally or mechanically verified.

        Returns:
            True for FORMALLY_VERIFIED and MECHANICALLY_CHECKED statuses.
        """
        return self.status in (
            TheoremStatus.FORMALLY_VERIFIED,
            TheoremStatus.MECHANICALLY_CHECKED,
        )

    def has_dependency(self, theorem_id: str) -> bool:
        """Check whether this theorem directly depends on another theorem.

        Args:
            theorem_id: The theorem ID to check for in the dependencies tuple.

        Returns:
            True if theorem_id is a direct dependency of this theorem.
        """
        return theorem_id in self.dependencies

    def summary(self) -> str:
        """Return a concise one-line summary of the theorem.

        Returns:
            String in the format: 'TheoremID (section_ref) [STATUS] — first 80 chars of statement'.
        """
        short_stmt = self.statement[:80] + ("…" if len(self.statement) > 80 else "")
        return f"{self.theorem_id} ({self.section_ref}) [{self.status.value}] — {short_stmt}"

    def dependency_count(self) -> int:
        """Return the number of direct dependencies of this theorem.

        Returns:
            Integer count of entries in the dependencies tuple.
        """
        return len(self.dependencies)

    def fingerprint(self) -> str:
        """Compute a SHA-256 fingerprint from theorem_id and statement.

        Returns:
            Lowercase hex-encoded SHA-256 digest string.
        """
        raw = (self.theorem_id + self.statement).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Module-level theorem constants — Ch23 canonical theorem set
# ---------------------------------------------------------------------------

THEOREM_EXEC_SECTION_INJECTION = TheoremRecord(
    theorem_id="Ch23.T1",
    statement=(
        "exec() inserts a new dynamic section at the caller's coordinate with proposal-tier "
        "trust. Formally: let e be an exec coordinate, let c be a source code string. "
        "Executing exec(c, G, L) at coordinate e inserts a section σ_c into the sheaf S "
        "at stalk S(e), where σ_c.namespace = names(c), σ_c.trust = PROPOSAL, and "
        "σ_c.support ⊆ support(e). No exec injection can self-elevate its trust tier."
    ),
    proof_sketch=(
        "By definition of section injection (§1.1), an exec call at coordinate e is modelled "
        "as a morphism f_c: ∅ → S(e) that introduces the section σ_c. "
        "The trust ceiling PROPOSAL is enforced by the trust assignment function τ, which maps "
        "any injected section to the minimal tier when no external corroboration exists. "
        "The support constraint σ_c.support ⊆ support(e) follows from the locality axiom: "
        "a section at stalk e cannot claim support outside e's neighbourhood. "
        "Since τ is monotone and no corroboration has been recorded at injection time, "
        "the trust ceiling PROPOSAL holds at the moment of injection and by monotonicity "
        "cannot be silently raised without an explicit external signal."
    ),
    theory_chapter=23,
    section_ref="Ch23 §1.1",
    status=TheoremStatus.SKETCH_ONLY,
    dependencies=(),
    proof_method=ProofMethod.SHEAF_THEORY,
)

THEOREM_EVAL_QUERY_SEMANTICS = TheoremRecord(
    theorem_id="Ch23.T2",
    statement=(
        "eval() queries the current section value at the caller's coordinate and returns it "
        "with trust bounded by min(trust(σ), PROPOSAL). Formally: given a well-formed "
        "expression expr and coordinate e, eval(expr, G, L) computes the value v = ⟦expr⟧_{S(e)}, "
        "and the resulting EvalResult carries trust τ_result = min(τ(S(e)), PROPOSAL). "
        "In particular, no eval result can exceed PROPOSAL tier without external corroboration, "
        "even if the underlying section is at a higher trust tier."
    ),
    proof_sketch=(
        "The trust bound for eval results follows from the read-only nature of eval: it does not "
        "modify any section but it does introduce a new value object into the dynamic environment. "
        "Because the new value object is untethered from any established verification chain, "
        "the trust assignment function τ applies the proposal ceiling. "
        "The expression ⟦expr⟧_{S(e)} is evaluated within the restriction map ρ_{e→base}(S) "
        "so it can only read, never write, higher-trust sections. "
        "The min operation in τ_result ensures that even if individual symbols within expr have "
        "VERIFIED trust, the composed result is not automatically promoted."
    ),
    theory_chapter=23,
    section_ref="Ch23 §1.2",
    status=TheoremStatus.SKETCH_ONLY,
    dependencies=(),
    proof_method=ProofMethod.OPERATIONAL_SEMANTICS,
)

THEOREM_MONKEY_PATCH_INVALIDATION = TheoremRecord(
    theorem_id="Ch23.T3",
    statement=(
        "Applying a monkey patch to attribute A invalidates all sections that depend on A's "
        "original value. Formally: let P be a monkey patch that replaces attr A with a new "
        "value v'. Let D(A) = {σ | σ depends on A} be the dependency set. After applying P, "
        "every σ ∈ D(A) is moved to INVALIDATED status and must be re-verified before any "
        "judgment can rely on σ. The invalidation is complete: no section in D(A) may remain "
        "at VERIFIED or higher trust after P is applied."
    ),
    proof_sketch=(
        "The dependency relation D is defined via the restriction maps of the sheaf: σ depends "
        "on A iff the evaluation of σ at any compatible coordinate requires the value of A. "
        "When P replaces A with v', the stalk at A's coordinate is modified, breaking all "
        "restriction paths that factor through A. "
        "By the sheaf gluing axiom, any section that cannot be reconstructed from consistent "
        "local data is invalid; since sections in D(A) were constructed assuming A = v ≠ v', "
        "they fail the compatibility check. "
        "The completeness of the invalidation (no partial invalidation) follows from the "
        "fact that the restriction maps are total: if σ depends on A at all, every stalk "
        "of σ is potentially tainted."
    ),
    theory_chapter=23,
    section_ref="Ch23 §2.1",
    status=TheoremStatus.SKETCH_ONLY,
    dependencies=(),
    proof_method=ProofMethod.SHEAF_THEORY,
)

THEOREM_PATCH_STACK_ORDERING = TheoremRecord(
    theorem_id="Ch23.T4",
    statement=(
        "The patch stack is strictly ordered; reverting patch k restores exactly the state "
        "prior to patch k's application. Formally: let (P_1, P_2, …, P_n) be a stack of "
        "monkey patches applied sequentially. Reverting P_k (for any 1 ≤ k ≤ n) yields a "
        "sheaf state S' such that S' = S_{k-1}, where S_{k-1} is the state after applying "
        "P_1, …, P_{k-1} but before applying P_k. "
        "Reversion is idempotent: reverting P_k twice has no additional effect beyond the "
        "first reversion."
    ),
    proof_sketch=(
        "Each patch P_k stores a backup snapshot B_k = (A, v_old) of the attribute it "
        "replaces. Reverting P_k replaces the current value v_new with v_old, restoring "
        "the stalk at A's coordinate to its pre-P_k state. "
        "The ordering guarantee follows from the stack discipline: no subsequent patch P_j "
        "(j > k) in the reverted prefix can affect the restored value because those patches "
        "have not been applied in the reverted world. "
        "Idempotency holds because after the first reversion, the attribute already holds v_old, "
        "so a second reversion writes v_old again (a no-op). "
        "The invariant S' = S_{k-1} is maintained by induction on the stack depth."
    ),
    theory_chapter=23,
    section_ref="Ch23 §2.2",
    status=TheoremStatus.SKETCH_ONLY,
    dependencies=("Ch23.T3",),
    proof_method=ProofMethod.INDUCTION,
)

THEOREM_HOT_RELOAD_DESCENT = TheoremRecord(
    theorem_id="Ch23.T5",
    statement=(
        "Hot reload is a valid incremental descent step if and only if all replacement sections "
        "agree on their mutual overlaps. Formally: a hot reload R replacing sections "
        "{σ_1', σ_2', …, σ_m'} is a valid descent iff for every pair (i, j) with i ≠ j, "
        "the restrictions of σ_i' and σ_j' to their overlap coordinate e_{ij} satisfy "
        "ρ_{i→ij}(σ_i') = ρ_{j→ij}(σ_j'). "
        "If this condition fails for any pair, the reload is rejected as an invalid descent."
    ),
    proof_sketch=(
        "A descent in the sheaf-theoretic sense requires that locally consistent data can be "
        "assembled into a global section. The replacement sections {σ_i'} form such a descent "
        "datum if and only if they satisfy the gluing compatibility condition on all pairwise "
        "overlaps (the standard sheaf descent axiom). "
        "The hot reload mechanism checks these pairwise restrictions before committing any "
        "section; if any check fails, the partial reload is aborted and the original sections "
        "are restored via the patch-stack rollback mechanism (Ch23.T4). "
        "When all overlaps are consistent, the gluing theorem guarantees a unique global section "
        "that extends all σ_i', giving the valid descent step."
    ),
    theory_chapter=23,
    section_ref="Ch23 §3.1",
    status=TheoremStatus.SKETCH_ONLY,
    dependencies=("Ch23.T3", "Ch23.T4"),
    proof_method=ProofMethod.SHEAF_THEORY,
)

THEOREM_RELOAD_ROLLBACK = TheoremRecord(
    theorem_id="Ch23.T6",
    statement=(
        "A failed hot reload can be fully rolled back if and only if all individual descent "
        "steps are individually reversible. Formally: let R = (s_1, s_2, …, s_m) be a sequence "
        "of descent steps comprising a hot reload. R is fully rollback-able iff each step s_i "
        "stores a reversible backup snapshot b_i such that applying b_i to the sheaf after s_i "
        "yields the pre-s_i state. "
        "Equivalently, the composite rollback R^{-1} = (s_m^{-1}, …, s_1^{-1}) applied in "
        "reverse order restores the original sheaf state S_0."
    ),
    proof_sketch=(
        "Each descent step s_i is implemented as a pair (apply, undo), where undo stores the "
        "original section and restores it on demand. "
        "Reversibility of s_i is therefore guaranteed by construction when the backup snapshot "
        "b_i is created immediately before s_i executes. "
        "The full rollback R^{-1} applies undos in reverse order; since the steps are ordered "
        "and each undo targets its own section independently, no step-ordering conflicts arise. "
        "The only failure mode is a missing backup (e.g., storage error during snapshot creation); "
        "the theorem therefore requires that every b_i exists, which is enforced by the "
        "pre-commit snapshot protocol described in §3.2."
    ),
    theory_chapter=23,
    section_ref="Ch23 §3.2",
    status=TheoremStatus.SKETCH_ONLY,
    dependencies=("Ch23.T4", "Ch23.T5"),
    proof_method=ProofMethod.DIRECT_CONSTRUCTION,
)

THEOREM_DYNAMIC_SECTION_TRUST = TheoremRecord(
    theorem_id="Ch23.T7",
    statement=(
        "Dynamic sections generated by exec or eval are bounded at proposal-tier trust until "
        "externally corroborated. Formally: let σ be any section introduced via an exec or eval "
        "call. Then τ(σ) = PROPOSAL at injection time, and τ(σ) may only be raised to a higher "
        "tier by an explicit external corroboration signal c from a source with trust ≥ τ(σ). "
        "No amount of internal self-reference or repeated exec/eval can raise τ(σ) above "
        "PROPOSAL without such an external signal."
    ),
    proof_sketch=(
        "By Ch23.T1 (THEOREM_EXEC_SECTION_INJECTION), every exec-injected section enters the "
        "sheaf at PROPOSAL trust. By Ch23.T2 (THEOREM_EVAL_QUERY_SEMANTICS), every eval result "
        "is also bounded at PROPOSAL. "
        "The trust assignment function τ is defined to be monotone under external corroboration "
        "and anti-monotone under mutation: patching a section can only lower trust, never raise it. "
        "Since internal exec/eval calls are themselves at PROPOSAL, their corroboration signals "
        "cannot raise τ(σ) above PROPOSAL — the trust ceiling of the corroborating source limits "
        "the achievable tier for the corroborated section. "
        "Therefore τ(σ) = PROPOSAL is an invariant until a corroboration signal from a "
        "strictly higher-trust external source is received."
    ),
    theory_chapter=23,
    section_ref="Ch23 §1.3",
    status=TheoremStatus.SKETCH_ONLY,
    dependencies=("Ch23.T1", "Ch23.T2"),
    proof_method=ProofMethod.SHEAF_THEORY,
)

THEOREM_INVALIDATION_CASCADE = TheoremRecord(
    theorem_id="Ch23.T8",
    statement=(
        "The invalidation cascade from a monkey patch terminates in a finite number of steps "
        "under the acyclicity assumption on the dependency graph. Formally: let G = (V, E) be "
        "the dependency graph where V is the set of attributes and sections, and there is an "
        "edge from v to u if v depends on u. If G is a DAG (directed acyclic graph), then for "
        "any patched attribute A, the BFS cascade from A visits at most |V| nodes, and "
        "terminates in O(|V| + |E|) time. "
        "If G contains a cycle, termination is not guaranteed without the cascade_limit bound."
    ),
    proof_sketch=(
        "In a DAG, BFS starting from A visits each node at most once because there are no "
        "back-edges: once a node v is added to the visited set it cannot be reached again "
        "via a different path without a cycle. "
        "The BFS queue therefore contains each node at most once, and since |V| is finite, "
        "the cascade terminates after at most |V| dequeue operations. "
        "The O(|V| + |E|) bound follows from the standard BFS complexity analysis. "
        "In the presence of cycles (when the acyclicity assumption fails), the cascade_limit "
        "parameter provides a hard bound on the number of sections processed, ensuring "
        "termination at the cost of potentially incomplete invalidation."
    ),
    theory_chapter=23,
    section_ref="Ch23 §2.3",
    status=TheoremStatus.SKETCH_ONLY,
    dependencies=("Ch23.T3",),
    proof_method=ProofMethod.INDUCTION,
)


# ---------------------------------------------------------------------------
# TheoremProver
# ---------------------------------------------------------------------------


@dataclass
class TheoremProver:
    """Provides verification and proof-checking utilities for the Ch23 theorem library.

    In a full implementation, this class would interface with a proof assistant
    such as Lean 4 or Coq. In the current implementation, it performs structural
    consistency checks and proof sketch validation: it ensures that every
    registered theorem has non-empty statement and proof_sketch fields, that all
    declared dependencies are themselves registered, and that the dependency
    graph is acyclic.
    """

    _theorems: dict[str, TheoremRecord] = field(default_factory=dict)
    _verification_log: list[dict] = field(default_factory=list)

    def register(self, theorem: TheoremRecord) -> None:
        """Register a theorem in the prover's internal dictionary.

        Args:
            theorem: The TheoremRecord to register. Keyed by theorem_id.
        """
        self._theorems[theorem.theorem_id] = theorem

    def verify_theorem(self, theorem_id: str) -> dict:
        """Attempt structural verification of a registered theorem.

        Checks that:
        1. The theorem exists in the registry.
        2. Its statement is non-empty.
        3. Its proof_sketch is non-empty.
        4. All declared dependencies are themselves registered.

        Args:
            theorem_id: ID of the theorem to verify.

        Returns:
            Verification result dict with theorem_id, passed, checks dict,
            issues list, and verified_at timestamp.
        """
        issues: list[str] = []
        checks: dict[str, bool] = {}

        theorem = self._theorems.get(theorem_id)
        if theorem is None:
            return {
                "theorem_id": theorem_id,
                "passed": False,
                "checks": {"theorem_registered": False},
                "issues": [f"Theorem '{theorem_id}' is not registered"],
                "verified_at": time.time(),
            }

        checks["theorem_registered"] = True
        checks["statement_non_empty"] = bool(theorem.statement.strip())
        checks["proof_sketch_non_empty"] = bool(theorem.proof_sketch.strip())

        missing_deps: list[str] = []
        for dep_id in theorem.dependencies:
            if dep_id not in self._theorems:
                missing_deps.append(dep_id)
        checks["all_deps_registered"] = len(missing_deps) == 0

        if not checks["statement_non_empty"]:
            issues.append("Statement field is empty")
        if not checks["proof_sketch_non_empty"]:
            issues.append("Proof sketch field is empty")
        if missing_deps:
            issues.append(f"Unregistered dependencies: {missing_deps}")

        passed = all(checks.values())
        result = {
            "theorem_id": theorem_id,
            "passed": passed,
            "checks": checks,
            "issues": issues,
            "verified_at": time.time(),
        }
        self._verification_log.append(result)
        return result

    def list_theorems(self) -> list[str]:
        """Return a sorted list of all registered theorem IDs.

        Returns:
            Sorted list of theorem_id strings.
        """
        return sorted(self._theorems.keys())

    def check_dependencies(self, theorem_id: str) -> dict:
        """Check dependency satisfaction for a theorem.

        For each declared dependency, checks whether it is registered and
        whether it passes structural verification.

        Args:
            theorem_id: ID of the theorem whose dependencies to check.

        Returns:
            Dict with theorem_id, dependencies list, satisfied list,
            unsatisfied list, and all_satisfied flag.
        """
        theorem = self._theorems.get(theorem_id)
        if theorem is None:
            return {
                "theorem_id": theorem_id,
                "dependencies": [],
                "satisfied": [],
                "unsatisfied": [f"Theorem '{theorem_id}' not found"],
                "all_satisfied": False,
            }

        satisfied: list[str] = []
        unsatisfied: list[str] = []

        for dep_id in theorem.dependencies:
            if dep_id in self._theorems:
                verified = self.verify_theorem(dep_id)
                if verified["passed"]:
                    satisfied.append(dep_id)
                else:
                    unsatisfied.append(dep_id)
            else:
                unsatisfied.append(dep_id)

        return {
            "theorem_id": theorem_id,
            "dependencies": list(theorem.dependencies),
            "satisfied": satisfied,
            "unsatisfied": unsatisfied,
            "all_satisfied": len(unsatisfied) == 0,
        }

    def proof_status_report(self) -> str:
        """Return a multi-line proof status report for all registered theorems.

        Reports total theorem count, breakdown by TheoremStatus, list of
        unverified theorems, and overall verified percentage.

        Returns:
            Formatted multi-line report string.
        """
        theorems = list(self._theorems.values())
        total = len(theorems)
        if total == 0:
            return "No theorems registered."

        by_status: dict[str, int] = {}
        for t in theorems:
            key = t.status.value
            by_status[key] = by_status.get(key, 0) + 1

        verified_count = sum(1 for t in theorems if t.is_verified())
        unverified = [t.theorem_id for t in theorems if not t.is_verified()]
        verified_pct = round(100.0 * verified_count / total, 1)

        lines = [
            "Proof Status Report",
            f"  Total theorems:   {total}",
            f"  Verified:         {verified_count} ({verified_pct}%)",
            "  By status:",
        ]
        for status_val, count in sorted(by_status.items()):
            lines.append(f"    {status_val:30s}: {count}")
        lines.append("  Unverified theorems:")
        for tid in sorted(unverified):
            lines.append(f"    - {tid}")
        return "\n".join(lines)

    def validate_consistency(self) -> dict:
        """Validate the entire theorem registry for structural consistency.

        Checks for:
        - Circular dependency chains (DFS).
        - Unregistered dependencies.
        - Theorems with empty proof sketches.

        Returns:
            Dict with is_consistent flag and list of issue strings.
        """
        issues: list[str] = []

        # Check all deps are registered and proofs non-empty
        for theorem in self._theorems.values():
            if not theorem.proof_sketch.strip():
                issues.append(f"{theorem.theorem_id}: empty proof_sketch")
            for dep_id in theorem.dependencies:
                if dep_id not in self._theorems:
                    issues.append(f"{theorem.theorem_id}: dependency '{dep_id}' not registered")

        # Circular dependency detection via DFS
        def has_cycle(start: str) -> bool:
            visited: set[str] = set()
            stack: list[str] = [start]
            path: set[str] = set()
            while stack:
                node = stack[-1]
                if node not in visited:
                    visited.add(node)
                    path.add(node)
                    theorem = self._theorems.get(node)
                    deps = list(theorem.dependencies) if theorem else []
                    stack.extend(deps)
                else:
                    stack.pop()
                    path.discard(node)
            return False

        for tid in self._theorems:
            # Proper DFS for cycle detection
            def dfs_cycle(node: str, visiting: set[str], visited: set[str]) -> bool:
                if node in visiting:
                    return True
                if node in visited:
                    return False
                visiting.add(node)
                t = self._theorems.get(node)
                if t:
                    for dep in t.dependencies:
                        if dfs_cycle(dep, visiting, visited):
                            return True
                visiting.discard(node)
                visited.add(node)
                return False

            visiting: set[str] = set()
            visited: set[str] = set()
            if dfs_cycle(tid, visiting, visited):
                issues.append(f"Circular dependency detected involving '{tid}'")

        return {
            "is_consistent": len(issues) == 0,
            "issues": issues,
        }

    def theorem_graph(self) -> dict:
        """Return the dependency graph as an adjacency dict.

        Returns:
            Dict mapping each theorem_id to its list of direct dependency IDs.
        """
        return {
            tid: list(t.dependencies)
            for tid, t in self._theorems.items()
        }

    def export_theorems(self) -> list[dict]:
        """Return all registered theorems as a list of serialised dicts.

        Returns:
            List of theorem.to_dict() results, sorted by theorem_id.
        """
        return [
            self._theorems[tid].to_dict()
            for tid in sorted(self._theorems.keys())
        ]


# ---------------------------------------------------------------------------
# TheoremLibrary
# ---------------------------------------------------------------------------


@dataclass
class TheoremLibrary:
    """Registry of all Ch23 theorems with lookup, filtering, and reporting.

    Pre-populated with the eight canonical theorems from Ch23 of theory2.tex
    via the initialize_ch23() method. The library delegates proof-checking
    operations to an internal TheoremProver instance.
    """

    _library: dict[str, TheoremRecord] = field(default_factory=dict)
    _prover: TheoremProver = field(default_factory=TheoremProver)

    def initialize_ch23(self) -> None:
        """Register all eight Ch23 module-level theorems into the library.

        Registers each of the eight canonical theorems into both the internal
        _library dict and the _prover instance, making them available for
        lookup, filtering, and proof checking.
        """
        for theorem in [
            THEOREM_EXEC_SECTION_INJECTION,
            THEOREM_EVAL_QUERY_SEMANTICS,
            THEOREM_MONKEY_PATCH_INVALIDATION,
            THEOREM_PATCH_STACK_ORDERING,
            THEOREM_HOT_RELOAD_DESCENT,
            THEOREM_RELOAD_ROLLBACK,
            THEOREM_DYNAMIC_SECTION_TRUST,
            THEOREM_INVALIDATION_CASCADE,
        ]:
            self._library[theorem.theorem_id] = theorem
            self._prover.register(theorem)

    def get(self, theorem_id: str) -> TheoremRecord | None:
        """Return the TheoremRecord with the given ID, or None if not found.

        Args:
            theorem_id: The theorem ID to look up.

        Returns:
            TheoremRecord if found, None otherwise.
        """
        return self._library.get(theorem_id)

    def list_all(self) -> list[TheoremRecord]:
        """Return all registered theorems sorted by theorem_id.

        Returns:
            Sorted list of TheoremRecord objects.
        """
        return [self._library[tid] for tid in sorted(self._library.keys())]

    def by_section(self, section_ref: str) -> list[TheoremRecord]:
        """Return theorems whose section_ref matches the given string.

        Args:
            section_ref: The section reference string to filter by (exact match).

        Returns:
            List of matching TheoremRecord objects.
        """
        return [t for t in self._library.values() if t.section_ref == section_ref]

    def by_status(self, status: TheoremStatus) -> list[TheoremRecord]:
        """Return theorems with the given TheoremStatus.

        Args:
            status: The TheoremStatus enum value to filter by.

        Returns:
            List of matching TheoremRecord objects sorted by theorem_id.
        """
        return sorted(
            [t for t in self._library.values() if t.status == status],
            key=lambda t: t.theorem_id,
        )

    def dependency_chain(self, theorem_id: str) -> list[str]:
        """Return the full transitive dependency chain for a theorem.

        Uses DFS to collect all theorem IDs that must hold for the given
        theorem to be valid, including transitively-required theorems.

        Args:
            theorem_id: Root theorem to compute the dependency chain for.

        Returns:
            Sorted list of theorem IDs in the transitive dependency chain
            (excluding the root theorem itself).
        """
        visited: set[str] = set()
        chain: list[str] = []
        stack: list[str] = list(
            self._library[theorem_id].dependencies
            if theorem_id in self._library
            else []
        )
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            chain.append(current)
            theorem = self._library.get(current)
            if theorem:
                stack.extend(theorem.dependencies)
        return sorted(chain)

    def library_report(self) -> str:
        """Return a multi-line report of the entire theorem library.

        Includes total count, per-theorem summaries, dependency information,
        and an overall consistency check result.

        Returns:
            Formatted multi-line report string.
        """
        theorems = self.list_all()
        consistency = self._prover.validate_consistency()
        lines = [
            f"Ch23 Theorem Library Report",
            f"  Total theorems:   {len(theorems)}",
            f"  Consistent:       {consistency['is_consistent']}",
            "",
            "  Theorems:",
        ]
        for t in theorems:
            dep_chain = self.dependency_chain(t.theorem_id)
            lines.append(f"    {t.summary()}")
            if dep_chain:
                lines.append(f"      Deps: {', '.join(dep_chain)}")
        if consistency["issues"]:
            lines.append("")
            lines.append("  Consistency issues:")
            for issue in consistency["issues"]:
                lines.append(f"    - {issue}")
        return "\n".join(lines)

    def count(self) -> int:
        """Return the number of theorems in the library.

        Returns:
            Integer count of registered theorems.
        """
        return len(self._library)


# ---------------------------------------------------------------------------
# Module-level default library instance, pre-populated with Ch23 theorems
# ---------------------------------------------------------------------------

DEFAULT_LIBRARY = TheoremLibrary()
DEFAULT_LIBRARY.initialize_ch23()


__all__ = [
    "TheoremStatus",
    "ProofMethod",
    "TheoremRecord",
    "TheoremProver",
    "TheoremLibrary",
    "DEFAULT_LIBRARY",
    "THEOREM_EXEC_SECTION_INJECTION",
    "THEOREM_EVAL_QUERY_SEMANTICS",
    "THEOREM_MONKEY_PATCH_INVALIDATION",
    "THEOREM_PATCH_STACK_ORDERING",
    "THEOREM_HOT_RELOAD_DESCENT",
    "THEOREM_RELOAD_ROLLBACK",
    "THEOREM_DYNAMIC_SECTION_TRUST",
    "THEOREM_INVALIDATION_CASCADE",
]

# copilot: Ch23 theorem library for live_mutation
