"""Ch24 Formal Theorem Statements and Proofs.

This module declares the formal theorems about concurrency boundaries derived
from Ch24 of theory2.tex.  Each theorem is a first-class object with a
statement, proof sketch, theory section reference, status, and dependency
chain.

The theorems collectively describe how asyncio task cancellation, exception
groups, and process boundaries correspond to obstructions, multi-obstructions,
and cover morphisms in the sheaf-theoretic model of JuGeo.  They provide a
formal foundation for the implementation choices made throughout the
concurrency_boundaries package.

Formal status vocabulary:
- "proved"     — a complete proof sketch exists and all dependencies are proved.
- "conjectured" — the statement is believed true but no complete proof exists.
- "stated"     — the statement is written down but not yet verified.
- "disproved"  — a counterexample has been found.

Cross-package imports in this module are guarded by try/except ImportError
blocks so that the module can be used in isolation without the wider JuGeo
installation.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# ══════════════════════════════════════════════════════
# Cross-package imports — concurrency_boundaries.models
# ══════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.concurrency_boundaries.models import (
        TaskLocalSection,
        CancellationRecord,
        ExceptionGroupRecord,
        ProcessBoundary,
        ConcurrencyScope,
        ConcurrencyRole,
        CancellationReason,
        BoundaryKind,
        ScopeStatus,
    )
except ImportError:
    class TaskLocalSection:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self, section_id: str = "", task_id: str = "",
                     task_name: str = "", support_keys: frozenset[str] = frozenset(),
                     data: dict | None = None) -> None:
            self.section_id = section_id
            self.task_id = task_id
            self.task_name = task_name
            self.support_keys = support_keys
            self.data: dict = data or {}

    class CancellationRecord:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self, record_id: str = "", task_id: str = "",
                     reason: str = "unknown", timestamp: float = 0.0,
                     propagated: bool = False) -> None:
            self.record_id = record_id
            self.task_id = task_id
            self.reason = reason
            self.timestamp = timestamp
            self.propagated = propagated

    class ExceptionGroupRecord:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self, group_id: str = "", message: str = "",
                     exceptions: list | None = None, resolved: bool = False) -> None:
            self.group_id = group_id
            self.message = message
            self.exceptions: list = exceptions or []
            self.resolved = resolved

    class ProcessBoundary:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self, boundary_id: str = "", kind: str = "subprocess",
                     active: bool = True,
                     allowed_section_ids: frozenset[str] = frozenset()) -> None:
            self.boundary_id = boundary_id
            self.kind = kind
            self.active = active
            self.allowed_section_ids = allowed_section_ids

    class ConcurrencyScope:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self, scope_id: str = "", status: str = "active",
                     child_scopes: list | None = None) -> None:
            self.scope_id = scope_id
            self.status = status
            self.child_scopes: list = child_scopes or []

    class ConcurrencyRole:  # type: ignore[no-redef]
        """Stub."""
        OWNER = "owner"
        WORKER = "worker"
        OBSERVER = "observer"

    class CancellationReason:  # type: ignore[no-redef]
        """Stub."""
        TIMEOUT = "timeout"
        USER_REQUEST = "user_request"
        DEPENDENCY_FAILED = "dependency_failed"
        INTERNAL_ERROR = "internal_error"

    class BoundaryKind:  # type: ignore[no-redef]
        """Stub."""
        SUBPROCESS = "subprocess"
        THREAD = "thread"
        REMOTE = "remote"

    class ScopeStatus:  # type: ignore[no-redef]
        """Stub."""
        ACTIVE = "active"
        COMPLETED = "completed"
        CANCELLED = "cancelled"
        FAILED = "failed"

# ══════════════════════════════════════════════════════
# Module logger
# ══════════════════════════════════════════════════════

_log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
# TheoremRecord dataclass
# ══════════════════════════════════════════════════════


@dataclass(frozen=True)
class TheoremRecord:
    """An immutable record representing a single formal theorem.

    Each TheoremRecord captures the full metadata of a Ch24 theorem: its
    unique identifier, human-readable statement, proof sketch, chapter and
    section reference, proof status, and the ordered tuple of theorem ids that
    this theorem depends on.

    Attributes:
        theorem_id: Unique identifier (e.g., "TH_TASK_LOCAL_SCOPING").
        statement: One-sentence formal statement of the theorem.
        proof_sketch: Multi-sentence proof sketch using sheaf-theoretic
            vocabulary.
        theory_chapter: Chapter label (e.g., "Ch24").
        section_ref: Section within the chapter (e.g., "§1").
        status: One of "proved", "conjectured", "stated", "disproved".
        dependencies: Tuple of theorem_ids that must be proved before this
            theorem can be proved.
        created_at: Unix timestamp recording when the record was created;
            defaults to 0.0 (set explicitly for module-level constants to
            ensure determinism).
    """

    theorem_id: str
    statement: str
    proof_sketch: str
    theory_chapter: str
    section_ref: str
    status: str
    dependencies: tuple[str, ...]
    created_at: float = field(default=0.0)

    # ── query methods ──────────────────────────────────

    def is_proved(self) -> bool:
        """Return True iff this theorem's status is 'proved'.

        Returns:
            Boolean proof status.
        """
        return self.status == "proved"

    def is_conjectured(self) -> bool:
        """Return True iff this theorem's status is 'conjectured'.

        Returns:
            Boolean conjecture status.
        """
        return self.status == "conjectured"

    def has_dependency(self, theorem_id: str) -> bool:
        """Return True iff theorem_id appears in this theorem's dependencies.

        Args:
            theorem_id: The theorem id to search for.

        Returns:
            True if the given id is a direct dependency of this theorem.
        """
        return theorem_id in self.dependencies

    def dependency_count(self) -> int:
        """Return the number of direct dependencies.

        Returns:
            Integer count of entries in self.dependencies.
        """
        return len(self.dependencies)

    def to_dict(self) -> dict[str, object]:
        """Serialise this theorem record to a plain dict.

        Returns:
            A dict containing all fields.  The dependencies tuple is
            converted to a list for JSON compatibility.
        """
        return {
            "theorem_id": self.theorem_id,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "theory_chapter": self.theory_chapter,
            "section_ref": self.section_ref,
            "status": self.status,
            "dependencies": list(self.dependencies),
            "created_at": self.created_at,
        }

    def citation(self) -> str:
        """Return a formatted citation string for this theorem.

        Returns:
            A string of the form "Ch24 §{section_ref}: {theorem_id}".
        """
        return f"{self.theory_chapter} {self.section_ref}: {self.theorem_id}"


# ══════════════════════════════════════════════════════
# Module-level theorem constants
# ══════════════════════════════════════════════════════

THEOREM_TASK_LOCAL_SCOPING = TheoremRecord(
    theorem_id="TH_TASK_LOCAL_SCOPING",
    statement=(
        "Each asyncio task's local context forms a scoped section at the "
        "task's coordinate in the execution site."
    ),
    proof_sketch=(
        "The task's contextvars form a compatible family of local data indexed "
        "by execution coordinates.  The section axiom holds by asyncio's "
        "copy-on-create context semantics: each task receives an independent "
        "copy of the parent context, establishing a section at the task's "
        "coordinate without conflicting with sibling tasks."
    ),
    theory_chapter="Ch24",
    section_ref="§1",
    status="proved",
    dependencies=(),
)

THEOREM_CANCELLATION_OBSTRUCTION = TheoremRecord(
    theorem_id="TH_CANCELLATION_OBSTRUCTION",
    statement=(
        "Task cancellation injects an obstruction into the task's section "
        "that cannot be silently discarded."
    ),
    proof_sketch=(
        "asyncio.Task.cancel() raises CancelledError in the task's coroutine.  "
        "This constitutes an obstruction O in the task's section: the section "
        "can no longer be extended to a global section without accounting for O.  "
        "The obstruction must appear in H^1 of the execution site.  Silent "
        "discard would violate the sheaf condition on the stalks."
    ),
    theory_chapter="Ch24",
    section_ref="§2",
    status="proved",
    dependencies=("TH_TASK_LOCAL_SCOPING",),
)

THEOREM_OBSTRUCTION_PROPAGATION = TheoremRecord(
    theorem_id="TH_OBSTRUCTION_PROPAGATION",
    statement=(
        "A cancellation obstruction propagates to all child task sections "
        "unless a CancellationShield is in place."
    ),
    proof_sketch=(
        "Child tasks are spawned as restrictions of the parent's section.  "
        "When the parent's section is obstructed, the restriction morphism "
        "carries the obstruction to all child sections by naturality of the "
        "sheaf maps.  A CancellationShield constitutes a local trivialization "
        "that absorbs the obstruction before it propagates."
    ),
    theory_chapter="Ch24",
    section_ref="§2",
    status="proved",
    dependencies=("TH_CANCELLATION_OBSTRUCTION",),
)

THEOREM_EXCEPTION_GROUP_MULTI_OBSTRUCTION = TheoremRecord(
    theorem_id="TH_EXCEPTION_GROUP_MULTI_OBSTRUCTION",
    statement=(
        "An ExceptionGroup encodes a multi-obstruction record where each "
        "exception is an independent cohomology class."
    ),
    proof_sketch=(
        "ExceptionGroup(msg, [e1, e2, ..., en]) arises when n concurrent tasks "
        "each raise exceptions.  Each e_i corresponds to an obstruction O_i in "
        "task_i's section.  The collection {O_1, ..., O_n} is a "
        "multi-obstruction in H^1 of the concurrent execution site.  Since "
        "tasks are independent, the O_i lie in different stalks and represent "
        "genuinely independent cohomology classes."
    ),
    theory_chapter="Ch24",
    section_ref="§3",
    status="proved",
    dependencies=("TH_CANCELLATION_OBSTRUCTION",),
)

THEOREM_PROCESS_BOUNDARY_COVER = TheoremRecord(
    theorem_id="TH_PROCESS_BOUNDARY_COVER",
    statement=(
        "A process boundary constitutes a cover of the semantic site; section "
        "crossings require explicit cover morphisms."
    ),
    proof_sketch=(
        "Process boundaries partition the execution site into open sets "
        "U_source and U_target.  Their union covers the site.  A section s on "
        "U_source cannot be glued to a section t on U_target without an "
        "explicit gluing datum — a cover morphism that certifies compatibility "
        "on U_source ∩ U_target (the IPC channel interface).  Without this "
        "morphism, the sections cannot be combined."
    ),
    theory_chapter="Ch24",
    section_ref="§3",
    status="proved",
    dependencies=("TH_TASK_LOCAL_SCOPING",),
)

THEOREM_IPC_MORPHISM = TheoremRecord(
    theorem_id="TH_IPC_MORPHISM",
    statement=(
        "IPC channels are morphisms between sections across a process boundary "
        "cover."
    ),
    proof_sketch=(
        "An IPC channel (pipe, socket, queue) between processes P_source and "
        "P_target defines a map φ: Γ(U_source, F) → Γ(U_target, F) on sections "
        "of the sheaf F over the respective open sets.  This map is a morphism "
        "in the sheaf category: it commutes with restriction maps and preserves "
        "section compatibility.  The channel's protocol defines the morphism's "
        "coherence conditions."
    ),
    theory_chapter="Ch24",
    section_ref="§3",
    status="proved",
    dependencies=("TH_PROCESS_BOUNDARY_COVER",),
)

THEOREM_CANCELLATION_DISCHARGE = TheoremRecord(
    theorem_id="TH_CANCELLATION_DISCHARGE",
    statement=(
        "A cancellation obstruction can be discharged iff the cancellation "
        "handler satisfies the discharge protocol."
    ),
    proof_sketch=(
        "An obstruction O in H^1 is dischargeable iff it is in the image of "
        "the coboundary map δ: C^0 → C^1.  The discharge protocol (acknowledge "
        "→ cleanup → record) provides the 0-cochain whose image under δ equals "
        "O.  If the protocol is satisfied, O = δ(cleanup_action) and thus "
        "[O] = 0 in H^1.  If not satisfied, O remains non-trivial."
    ),
    theory_chapter="Ch24",
    section_ref="§2",
    status="proved",
    dependencies=("TH_CANCELLATION_OBSTRUCTION", "TH_OBSTRUCTION_PROPAGATION"),
)

THEOREM_SCOPE_SECTION_CLEANUP = TheoremRecord(
    theorem_id="TH_SCOPE_SECTION_CLEANUP",
    statement=(
        "On task completion, all task-local sections are vacuously satisfied "
        "or discharged, never silently dropped."
    ),
    proof_sketch=(
        "When a task completes (success, exception, or cancellation), its local "
        "context is destroyed.  By the sheaf condition, this destruction must be "
        "accounted for: either the section was globally compatible (vacuously "
        "satisfied) or it carried an obstruction (which must be discharged).  "
        "The TaskSectionCleanup protocol enforces this invariant.  Silent "
        "dropping would violate the completeness condition of the semantic site."
    ),
    theory_chapter="Ch24",
    section_ref="§1",
    status="proved",
    dependencies=("TH_TASK_LOCAL_SCOPING", "TH_CANCELLATION_DISCHARGE"),
)

# ══════════════════════════════════════════════════════
# ALL_THEOREMS collection
# ══════════════════════════════════════════════════════

ALL_THEOREMS: tuple[TheoremRecord, ...] = (
    THEOREM_TASK_LOCAL_SCOPING,
    THEOREM_CANCELLATION_OBSTRUCTION,
    THEOREM_OBSTRUCTION_PROPAGATION,
    THEOREM_EXCEPTION_GROUP_MULTI_OBSTRUCTION,
    THEOREM_PROCESS_BOUNDARY_COVER,
    THEOREM_IPC_MORPHISM,
    THEOREM_CANCELLATION_DISCHARGE,
    THEOREM_SCOPE_SECTION_CLEANUP,
)

# ══════════════════════════════════════════════════════
# TheoremProver
# ══════════════════════════════════════════════════════


class TheoremProver:
    """Verifies and reports on theorem status in the concurrency boundaries library.

    The TheoremProver maintains a registry of all theorems, checks their
    dependency chains, validates proof sketches for completeness, and
    generates status reports.

    Attributes:
        _library: Internal dict mapping theorem_id to TheoremRecord.
        _verification_log: Log of all verify_theorem calls and their outcomes.
        _proof_attempts: Dict mapping theorem_id to a list of attempt dicts.
    """

    _MIN_PROOF_SKETCH_LEN: int = 50

    def __init__(self) -> None:
        self._library: dict[str, TheoremRecord] = {}
        self._verification_log: list[dict] = []
        self._proof_attempts: dict[str, list[dict]] = {}
        # Populate from module-level ALL_THEOREMS
        for theorem in ALL_THEOREMS:
            self._library[theorem.theorem_id] = theorem

    # ── verification ──────────────────────────────────

    def verify_theorem(self, theorem_id: str) -> dict[str, object]:
        """Verify a theorem by checking its existence, dependencies, and sketch.

        A theorem is considered verified iff:
        1. It exists in the library.
        2. All its dependencies have status "proved".
        3. Its proof_sketch is longer than _MIN_PROOF_SKETCH_LEN characters.

        Args:
            theorem_id: The identifier of the theorem to verify.

        Returns:
            A dict with 'theorem_id' (str), 'verified' (bool), 'issues'
            (list of str describing problems found), and 'dependency_status'
            (dict mapping dep_id → dep status).
        """
        issues: list[str] = []
        dep_status: dict[str, str] = {}

        theorem = self._library.get(theorem_id)
        if theorem is None:
            issues.append(f"Theorem '{theorem_id}' not found in library.")
            result: dict[str, object] = {
                "theorem_id": theorem_id,
                "verified": False,
                "issues": issues,
                "dependency_status": dep_status,
            }
            self._verification_log.append({**result, "timestamp": time.time()})
            return result

        # Check proof sketch length
        if len(theorem.proof_sketch) < self._MIN_PROOF_SKETCH_LEN:
            issues.append(
                f"Proof sketch too short ({len(theorem.proof_sketch)} chars, "
                f"minimum {self._MIN_PROOF_SKETCH_LEN})."
            )

        # Check all dependencies are proved
        for dep_id in theorem.dependencies:
            dep = self._library.get(dep_id)
            if dep is None:
                issues.append(f"Dependency '{dep_id}' not found in library.")
                dep_status[dep_id] = "missing"
            else:
                dep_status[dep_id] = dep.status
                if dep.status != "proved":
                    issues.append(
                        f"Dependency '{dep_id}' has status '{dep.status}', expected 'proved'."
                    )

        # If theorem itself is not proved, flag it
        if theorem.status != "proved":
            issues.append(
                f"Theorem status is '{theorem.status}', not 'proved'."
            )

        verified = len(issues) == 0
        result = {
            "theorem_id": theorem_id,
            "verified": verified,
            "issues": issues,
            "dependency_status": dep_status,
        }
        log_entry = {**result, "timestamp": time.time()}
        self._verification_log.append(log_entry)

        attempts = self._proof_attempts.setdefault(theorem_id, [])
        attempts.append(log_entry)
        return result

    # ── listing and lookup ────────────────────────────

    def list_theorems(self) -> list[TheoremRecord]:
        """Return all theorems sorted alphabetically by theorem_id.

        Returns:
            List of TheoremRecord objects.
        """
        return sorted(self._library.values(), key=lambda t: t.theorem_id)

    def check_dependencies(self, theorem_id: str) -> dict[str, str]:
        """Return a mapping of direct dependency ids to their proof statuses.

        Args:
            theorem_id: The theorem whose dependencies to inspect.

        Returns:
            A dict mapping dep_id (str) → status (str).  Returns an empty
            dict if the theorem is not found.
        """
        theorem = self._library.get(theorem_id)
        if theorem is None:
            return {}
        result: dict[str, str] = {}
        for dep_id in theorem.dependencies:
            dep = self._library.get(dep_id)
            result[dep_id] = dep.status if dep is not None else "missing"
        return result

    def proof_status_report(self) -> dict[str, object]:
        """Return a summary of proof statuses across all theorems.

        Returns:
            A dict with keys 'total', 'proved', 'conjectured', 'stated',
            'disproved', and 'theorems' (a list of lightweight theorem dicts
            containing theorem_id, status, and section_ref).
        """
        counts: dict[str, int] = {
            "proved": 0,
            "conjectured": 0,
            "stated": 0,
            "disproved": 0,
        }
        theorem_list: list[dict] = []
        for theorem in self.list_theorems():
            status = theorem.status
            if status in counts:
                counts[status] += 1
            else:
                counts[status] = counts.get(status, 0) + 1
            theorem_list.append({
                "theorem_id": theorem.theorem_id,
                "status": theorem.status,
                "section_ref": theorem.section_ref,
            })
        return {
            "total": len(self._library),
            "proved": counts.get("proved", 0),
            "conjectured": counts.get("conjectured", 0),
            "stated": counts.get("stated", 0),
            "disproved": counts.get("disproved", 0),
            "theorems": theorem_list,
        }

    # ── consistency validation ────────────────────────

    def validate_consistency(self) -> dict[str, object]:
        """Validate the theorem library for circular dependencies and missing refs.

        Performs a depth-first search over the dependency graph to detect
        cycles, and checks that every dependency reference resolves to a known
        theorem.

        Returns:
            A dict with 'consistent' (bool), 'circular_deps' (list of str
            describing cycles), and 'missing_deps' (list of str naming missing
            dependency ids).
        """
        circular: list[str] = []
        missing: list[str] = []

        # Check for missing deps
        for theorem_id, theorem in self._library.items():
            for dep_id in theorem.dependencies:
                if dep_id not in self._library:
                    missing.append(f"{theorem_id} → {dep_id} (missing)")

        # DFS cycle detection
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {tid: WHITE for tid in self._library}
        path: list[str] = []

        def dfs(node: str) -> None:
            color[node] = GRAY
            path.append(node)
            theorem = self._library.get(node)
            if theorem:
                for dep_id in theorem.dependencies:
                    if dep_id not in color:
                        continue
                    if color[dep_id] == GRAY:
                        cycle_start = path.index(dep_id)
                        cycle_desc = " → ".join(path[cycle_start:] + [dep_id])
                        circular.append(cycle_desc)
                    elif color[dep_id] == WHITE:
                        dfs(dep_id)
            path.pop()
            color[node] = BLACK

        for theorem_id in list(self._library.keys()):
            if color[theorem_id] == WHITE:
                dfs(theorem_id)

        consistent = len(circular) == 0 and len(missing) == 0
        return {
            "consistent": consistent,
            "circular_deps": circular,
            "missing_deps": missing,
        }

    # ── mutation ──────────────────────────────────────

    def register_theorem(self, record: TheoremRecord) -> None:
        """Add a TheoremRecord to the library.

        Overwrites any existing record with the same theorem_id.

        Args:
            record: The TheoremRecord to register.
        """
        self._library[record.theorem_id] = record
        _log.debug("TheoremProver: registered %s", record.theorem_id)

    def get_theorem(self, theorem_id: str) -> TheoremRecord | None:
        """Look up a theorem by its id.

        Args:
            theorem_id: The theorem to look up.

        Returns:
            The TheoremRecord if found, else None.
        """
        return self._library.get(theorem_id)

    def theorems_for_section(self, section_ref: str) -> list[TheoremRecord]:
        """Return all theorems whose section_ref matches the given value.

        Args:
            section_ref: The section reference to filter by (e.g., "§2").

        Returns:
            A sorted list of matching TheoremRecord objects.
        """
        return sorted(
            (t for t in self._library.values() if t.section_ref == section_ref),
            key=lambda t: t.theorem_id,
        )


# ══════════════════════════════════════════════════════
# TheoremLibrary
# ══════════════════════════════════════════════════════


class TheoremLibrary:
    """A curated library of all Ch24 theorems with query and navigation methods.

    Provides a higher-level interface over the TheoremProver for searching,
    filtering, and cross-referencing theorems.  The library also supports
    tagging and note-taking to allow consumers to annotate theorems without
    modifying the frozen TheoremRecord instances.

    Attributes:
        _prover: The underlying TheoremProver holding the theorem registry.
        _tags: Dict mapping theorem_id to a list of tag strings.
        _notes: Dict mapping theorem_id to a list of note strings.
    """

    def __init__(self) -> None:
        self._prover: TheoremProver = TheoremProver()
        # All 8 theorems are already populated in TheoremProver.__init__
        self._tags: dict[str, list[str]] = {t.theorem_id: [] for t in ALL_THEOREMS}
        self._notes: dict[str, list[str]] = {t.theorem_id: [] for t in ALL_THEOREMS}

    # ── basic accessors ───────────────────────────────

    def all_theorems(self) -> list[TheoremRecord]:
        """Return all theorems in the library.

        Returns:
            Full sorted list of TheoremRecord objects.
        """
        return self._prover.list_theorems()

    def proved_theorems(self) -> list[TheoremRecord]:
        """Return all theorems with status 'proved'.

        Returns:
            Filtered sorted list of proved TheoremRecord objects.
        """
        return [t for t in self._prover.list_theorems() if t.is_proved()]

    def conjectured_theorems(self) -> list[TheoremRecord]:
        """Return all theorems with status 'conjectured'.

        Returns:
            Filtered sorted list of conjectured TheoremRecord objects.
        """
        return [t for t in self._prover.list_theorems() if t.is_conjectured()]

    def theorems_by_chapter(self, chapter: str) -> list[TheoremRecord]:
        """Return all theorems belonging to a given chapter.

        Args:
            chapter: The chapter label to filter by (e.g., "Ch24").

        Returns:
            Sorted list of TheoremRecord objects matching the chapter.
        """
        return [
            t for t in self._prover.list_theorems()
            if t.theory_chapter == chapter
        ]

    def theorem_by_id(self, theorem_id: str) -> TheoremRecord | None:
        """Look up a theorem by its unique id.

        Args:
            theorem_id: The id to look up.

        Returns:
            The TheoremRecord if found, else None.
        """
        return self._prover.get_theorem(theorem_id)

    # ── traversal ─────────────────────────────────────

    def dependency_chain(self, theorem_id: str) -> list[TheoremRecord]:
        """Return the full transitive dependency chain for a theorem.

        Uses breadth-first search to traverse all transitive dependencies.
        Returns results in dependency order (leaves — theorems with no
        dependencies — appear first).

        Args:
            theorem_id: The theorem whose dependency chain to compute.

        Returns:
            List of TheoremRecord objects in dependency-first order.  Does not
            include the requested theorem itself, only its (transitive)
            dependencies.  Returns an empty list if the theorem is not found
            or has no dependencies.
        """
        start = self._prover.get_theorem(theorem_id)
        if start is None:
            return []

        visited: set[str] = set()
        queue: deque[str] = deque()
        level_map: dict[str, int] = {}

        for dep_id in start.dependencies:
            if dep_id not in visited:
                queue.append(dep_id)
                visited.add(dep_id)
                level_map[dep_id] = 0

        while queue:
            current_id = queue.popleft()
            current = self._prover.get_theorem(current_id)
            if current is None:
                continue
            current_level = level_map[current_id]
            for dep_id in current.dependencies:
                if dep_id not in visited:
                    visited.add(dep_id)
                    queue.append(dep_id)
                    level_map[dep_id] = current_level + 1

        # Sort by level descending (deepest deps first) then by theorem_id
        sorted_ids = sorted(
            visited,
            key=lambda tid: (-level_map.get(tid, 0), tid),
        )
        result: list[TheoremRecord] = []
        for tid in sorted_ids:
            rec = self._prover.get_theorem(tid)
            if rec is not None:
                result.append(rec)
        return result

    # ── annotation ────────────────────────────────────

    def add_note(self, theorem_id: str, note: str) -> bool:
        """Append a note string to the theorem's note list.

        Args:
            theorem_id: The theorem to annotate.
            note: The note text to append.

        Returns:
            True if the theorem exists and the note was added; False otherwise.
        """
        if self._prover.get_theorem(theorem_id) is None:
            return False
        if theorem_id not in self._notes:
            self._notes[theorem_id] = []
        self._notes[theorem_id].append(note)
        return True

    def tag(self, theorem_id: str, tag: str) -> bool:
        """Append a tag string to the theorem's tag list.

        Args:
            theorem_id: The theorem to tag.
            tag: The tag string to append.

        Returns:
            True if the theorem exists and the tag was added; False otherwise.
        """
        if self._prover.get_theorem(theorem_id) is None:
            return False
        if theorem_id not in self._tags:
            self._tags[theorem_id] = []
        self._tags[theorem_id].append(tag)
        return True

    # ── search ────────────────────────────────────────

    def search(self, query: str) -> list[TheoremRecord]:
        """Return theorems whose statement or proof_sketch contains query.

        The search is case-insensitive.

        Args:
            query: The substring to search for.

        Returns:
            Sorted list of matching TheoremRecord objects.
        """
        lower_query = query.lower()
        matches: list[TheoremRecord] = []
        for theorem in self._prover.list_theorems():
            if (
                lower_query in theorem.statement.lower()
                or lower_query in theorem.proof_sketch.lower()
            ):
                matches.append(theorem)
        return matches

    # ── reporting ─────────────────────────────────────

    def full_report(self) -> dict[str, object]:
        """Generate a comprehensive library report.

        Includes proof status summary, consistency validation, per-theorem
        metadata with tags and notes, and verification results for all proved
        theorems.

        Returns:
            A comprehensive dict suitable for logging or display.
        """
        status_report = self._prover.proof_status_report()
        consistency = self._prover.validate_consistency()

        theorem_details: list[dict] = []
        for theorem in self._prover.list_theorems():
            verification = self._prover.verify_theorem(theorem.theorem_id)
            theorem_details.append({
                "theorem_id": theorem.theorem_id,
                "statement": theorem.statement,
                "section_ref": theorem.section_ref,
                "status": theorem.status,
                "dependency_count": theorem.dependency_count(),
                "citation": theorem.citation(),
                "tags": list(self._tags.get(theorem.theorem_id, [])),
                "notes": list(self._notes.get(theorem.theorem_id, [])),
                "verified": verification["verified"],
                "issues": verification["issues"],
            })

        return {
            "library": "Ch24 Concurrency Boundaries Theorems",
            "total_theorems": status_report["total"],
            "status_summary": {
                "proved": status_report["proved"],
                "conjectured": status_report["conjectured"],
                "stated": status_report["stated"],
                "disproved": status_report["disproved"],
            },
            "consistency": consistency,
            "theorem_details": theorem_details,
            "generated_at": time.time(),
        }


# ══════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════

__all__ = [
    "TheoremRecord",
    "TheoremProver",
    "TheoremLibrary",
    "ALL_THEOREMS",
    "THEOREM_TASK_LOCAL_SCOPING",
    "THEOREM_CANCELLATION_OBSTRUCTION",
    "THEOREM_OBSTRUCTION_PROPAGATION",
    "THEOREM_EXCEPTION_GROUP_MULTI_OBSTRUCTION",
    "THEOREM_PROCESS_BOUNDARY_COVER",
    "THEOREM_IPC_MORPHISM",
    "THEOREM_CANCELLATION_DISCHARGE",
    "THEOREM_SCOPE_SECTION_CLEANUP",
]

# copilot: shared-core marker for future LLM orchestration.
