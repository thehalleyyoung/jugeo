"""Core data models for the bug_detection subsystem (theory2.tex Ch11).

All models are frozen dataclasses with full JSON round-trip support.
See theory2.tex Ch11 for the theoretical foundations of bug detection as
cohomological obstruction theory.

Theoretical overview
--------------------
In the sheaf-theoretic framework of theory2.tex, a *bug* is not merely a
runtime error or a failing test: it is a **cohomological obstruction** — an
element of the first Čech cohomology group H¹(U, D) computed over a covering U
of the coordinate space of the judgment sheaf Γ.

The four models in this module correspond directly to the four principal objects
required by the bug-detection mode defined in theory2.tex Ch11:

1. **BugReport** (Ch11 §11.1)
   A first-class, immutable record of a detected obstruction.  Every BugReport
   carries a ``cohomology_class`` string that names its equivalence class in
   H¹(U, D) and a ``trust_tier`` that encodes how the evidence for the bug was
   obtained (consistent with the trust-algebra ordering in theory2.tex §252).

2. **BugDetectionResult** (Ch11 §11.2)
   The aggregate output of one detection pass.  The ``bugs`` tuple is ordered
   by severity (descending) and contains only distinct cohomology classes (no
   duplicate obstructions).  The ``witness`` field carries the symbolic
   counterexample that first triggered the detection run.

3. **DetectionSession** (Ch11 §11.3)
   A monotone accumulator for BugReports across iterations.  Sessions are
   *mutable* because new bugs may be discovered as the analysis depth
   increases.  The ``trust_floor`` constrains which trust tiers are admitted
   as genuine obstructions (following the no-silent-trust-promotion principle).

4. **BugKind** (Ch11 §11.4)
   An enumeration of the eight canonical obstruction families recognised by
   JuGeo.  Every BugReport is labelled with exactly one BugKind.  The mapping
   from BugKind to cohomology class is injective: distinct kinds produce
   distinct elements of H¹.

Judgment tuples
---------------
All obstructions must eventually be expressed as judgment tuples::

    (c, φ, A, E, O, B, T, Π)

where:

* c  — coordinate in the sheaf site Γ
* φ  — local proposition (the claim that fails)
* A  — arity / type annotation
* E  — evidence bundle (items that witness or refute φ)
* O  — obstruction set (H¹ classes blocking φ)
* B  — binder context (scope chain)
* T  — trust annotation (TrustLevel)
* Π  — provenance chain

Trust is an *ordered algebra*, not a scalar float.  The ``severity`` field of
BugReport is a float in [0, 1] representing urgency for repair scheduling; it
is strictly separate from the trust level.

No silent trust promotion
-------------------------
Any bug discovered via oracle/copilot analysis enters at
``TrustLevel.ORACLE_PROPOSED`` and must be upgraded explicitly.  The
``trust_tier`` field stores the *string name* of the trust level so that
models remain importable without jugeo.judgments available at import time.

# copilot: bug_detection models -- theory2 ch11 obstruction data structures
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Optional internal imports with fallback
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import TrustLevel, ProvenanceSource
except ImportError:
    TrustLevel = Any  # type: ignore[assignment,misc]
    ProvenanceSource = Any  # type: ignore[assignment,misc]

try:
    from jugeo.errors import ObstructionRecord, RepairHint
except ImportError:
    ObstructionRecord = Any  # type: ignore[assignment,misc]
    RepairHint = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# ---------------------------------------------------------------------------
# Module-level provenance
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-bug-detection",
    "sequence": "11",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "models",
}

# ---------------------------------------------------------------------------
# BugKind
# ---------------------------------------------------------------------------


class BugKind(str, Enum):
    """Canonical obstruction families for the bug-detection mode.

    Each member corresponds to a distinct class of coherence failure in the
    judgment sheaf.  The mapping from BugKind to cohomology generator is
    injective: different kinds produce linearly independent elements of H¹.

    Theory basis (Ch11 §11.4)
    -------------------------
    H¹(U, D) decomposes along the eight obstruction families below.  The
    name of each member doubles as the cohomology generator label used when
    classifying a ``BugReport.cohomology_class``.

    Members
    -------
    TYPE_ERROR
        A type mismatch or ill-formed type annotation at some coordinate.
        In the Curry-Howard correspondence this is a proof that the wrong
        proposition was established.  Maps to H¹ generator ``σ_type``.

    LOGIC_ERROR
        The observable behaviour of a function or expression violates its
        stated logical invariant (e.g. a loop invariant, postcondition, or
        mathematical identity).  Maps to H¹ generator ``σ_logic``.

    SCOPE_VIOLATION
        A name is referenced outside its binding scope or after its binding
        has been invalidated.  In the sheaf picture this is a *gluing failure*
        between the section defined over the binding coordinate and the section
        accessed at the reference coordinate.  Maps to H¹ generator
        ``σ_scope``.

    PROTOCOL_VIOLATION
        A sequence of operations violates a declared state-machine or
        communication protocol.  The violation is visible only when the local
        sections of two or more coordinates are composed, making it a
        genuinely *global* obstruction.  Maps to H¹ generator ``σ_proto``.

    TRUST_VIOLATION
        An oracle- or copilot-produced value has been silently promoted to a
        higher trust tier without an explicit discharge step.  This is the
        formal counterpart of the no-silent-trust-promotion axiom in
        theory2.tex §252.  Maps to H¹ generator ``σ_trust``.

    RESOURCE_LEAK
        A resource (file handle, socket, lock, memory allocation) is acquired
        but not released on every execution path.  In the coverage-sheaf view
        this corresponds to a missing section over the "release" coordinate.
        Maps to H¹ generator ``σ_resource``.

    CONCURRENCY_HAZARD
        A data race, deadlock, or ordering violation in a concurrent or
        asynchronous computation.  These bugs are intrinsically two-coordinate
        obstructions (they require at least two concurrent execution sites).
        Maps to H¹ generator ``σ_conc``.

    SPECIFICATION_DEVIATION
        The implementation disagrees with a formal or informal specification
        that has been registered in the JuGeo manifest.  This is the
        complement of specification-satisfaction success.  Maps to H¹
        generator ``σ_spec``.
    """

    TYPE_ERROR = "TYPE_ERROR"
    LOGIC_ERROR = "LOGIC_ERROR"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"
    TRUST_VIOLATION = "TRUST_VIOLATION"
    RESOURCE_LEAK = "RESOURCE_LEAK"
    CONCURRENCY_HAZARD = "CONCURRENCY_HAZARD"
    SPECIFICATION_DEVIATION = "SPECIFICATION_DEVIATION"

    def cohomology_generator(self) -> str:
        """Return the H¹ generator label for this obstruction kind.

        Returns
        -------
        str
            A string of the form ``σ_<suffix>``.
        """
        _map: dict[str, str] = {
            "TYPE_ERROR": "σ_type",
            "LOGIC_ERROR": "σ_logic",
            "SCOPE_VIOLATION": "σ_scope",
            "PROTOCOL_VIOLATION": "σ_proto",
            "TRUST_VIOLATION": "σ_trust",
            "RESOURCE_LEAK": "σ_resource",
            "CONCURRENCY_HAZARD": "σ_conc",
            "SPECIFICATION_DEVIATION": "σ_spec",
        }
        return _map[self.value]

    def is_local(self) -> bool:
        """Return True if this obstruction is detectable at a single coordinate.

        Local obstructions (TYPE_ERROR, LOGIC_ERROR, SCOPE_VIOLATION,
        TRUST_VIOLATION) can in principle be diagnosed by inspecting a single
        AST node.  Global obstructions require composing sections across
        multiple coordinates.

        Returns
        -------
        bool
            True for locally detectable kinds.
        """
        return self in {
            BugKind.TYPE_ERROR,
            BugKind.LOGIC_ERROR,
            BugKind.SCOPE_VIOLATION,
            BugKind.TRUST_VIOLATION,
        }

    def severity_baseline(self) -> float:
        """Return a default severity baseline in [0, 1] for this kind.

        Severity baselines are informed by the repair cost estimates in
        theory2.tex Table 11.1.  Individual BugReport instances may override
        this baseline via their ``severity`` field.

        Returns
        -------
        float
            A float in [0, 1].
        """
        _baselines: dict[str, float] = {
            "TYPE_ERROR": 0.7,
            "LOGIC_ERROR": 0.8,
            "SCOPE_VIOLATION": 0.75,
            "PROTOCOL_VIOLATION": 0.85,
            "TRUST_VIOLATION": 0.9,
            "RESOURCE_LEAK": 0.65,
            "CONCURRENCY_HAZARD": 0.95,
            "SPECIFICATION_DEVIATION": 0.6,
        }
        return _baselines[self.value]


# ---------------------------------------------------------------------------
# BugReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BugReport:
    """First-class immutable record of a detected obstruction.

    In the sheaf-theoretic framework of theory2.tex Ch11, a BugReport is not
    merely an error log entry — it is a *cohomology class* representing an
    element [δ(φ)] ∈ H¹(U, D) where φ is the failing local section at
    coordinate c.  This class carries enough information to:

    1. Locate the obstruction in the source code (``coordinate``).
    2. Classify it within the H¹ decomposition (``kind``, ``cohomology_class``).
    3. Audit how it was discovered (``trust_tier``, ``provenance``).
    4. Schedule its repair (``severity``).
    5. Reproduce it independently (``counterexample``).

    Trust tier (theory2.tex §252)
    -----------------------------
    The ``trust_tier`` field stores the *name* of a ``TrustLevel`` enum member.
    Bugs discovered by static analysis enter at ``"ORACLE_PROPOSED"``; those
    witnessed by a failing test enter at ``"RUNTIME_WITNESSED"``; those
    discharged by a formal solver enter at ``"SOLVER_DISCHARGED"``.  Promotion
    between tiers is always explicit — there is no silent upgrade.

    Cohomology class (Ch11 §11.2)
    -----------------------------
    The ``cohomology_class`` field is a human-readable string naming the
    equivalence class of this obstruction in H¹(U, D).  The format is::

        <kind_generator>:<coordinate_hash>

    e.g. ``"σ_type:a3f2b1"``.  Two BugReports with the same cohomology_class
    are considered *equivalent* under the repair relation (repairing one
    automatically repairs the other).

    Judgment tuple encoding (Ch11 §11.1)
    --------------------------------------
    The ``judgment_tuple`` property returns the eight-component tuple
    ``(c, φ, A, E, O, B, T, Π)`` matching the theory2 encoding:

    * c — coordinate string
    * φ — description (the failing claim)
    * A — type_annotation or "?"
    * E — list of evidence items from provenance
    * O — [cohomology_class] (the obstruction set)
    * B — [] (binder context, filled by the bridge layer)
    * T — trust_tier label
    * Π — provenance dict serialised as list of pairs

    Parameters
    ----------
    bug_id:
        Unique identifier for this bug report (hex UUID prefix).
    kind:
        The ``BugKind`` classification of this obstruction.
    coordinate:
        The sheaf coordinate at which the obstruction was detected, typically
        ``"<filename>:<lineno>:<col>:<node_type>"``.
    severity:
        Float in [0, 1] representing urgency for repair scheduling.  Higher
        means more urgent.  This is distinct from the trust_tier.
    description:
        Human-readable description of the failing claim φ.
    counterexample:
        A concrete witness to the obstruction — may be a dict, string, or
        any JSON-serialisable value.
    trust_tier:
        String name of a ``TrustLevel`` enum member (e.g.
        ``"ORACLE_PROPOSED"``).  Defaults to ``"ORACLE_PROPOSED"`` in
        accordance with the no-silent-trust-promotion principle.
    cohomology_class:
        The H¹ class label for this obstruction.  Computed from kind and
        coordinate if not provided explicitly.
    provenance:
        Dictionary of provenance metadata: how, when, and by whom the bug
        was discovered.
    metadata:
        Additional key/value metadata for downstream consumers.
    """

    bug_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    kind: BugKind = BugKind.LOGIC_ERROR
    coordinate: str = ""
    severity: float = 0.5
    description: str = ""
    counterexample: Any = None
    trust_tier: str = "ORACLE_PROPOSED"
    cohomology_class: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def judgment_tuple(self) -> tuple[Any, ...]:
        """Return the eight-component judgment tuple (c,φ,A,E,O,B,T,Π).

        Maps this BugReport to the judgment encoding defined in theory2.tex
        §11.1.  The tuple is suitable for transmission to the repair_semantics
        pipeline as a structured counterexample.

        Returns
        -------
        tuple[Any, ...]
            Eight-element tuple ``(c, φ, A, E, O, B, T, Π)``.
        """
        c = self.coordinate
        phi = self.description
        a = self.metadata.get("type_annotation", "?")
        evidence: list[Any] = list(self.provenance.get("evidence_items", []))
        obstructions = [self.cohomology_class] if self.cohomology_class else []
        binders: list[Any] = list(self.metadata.get("scope_chain", []))
        trust = self.trust_tier
        pi = list(self.provenance.items())
        return (c, phi, a, evidence, obstructions, binders, trust, pi)

    def compute_cohomology_class(self) -> str:
        """Compute the canonical H¹ class label from kind and coordinate.

        The label format is ``<generator>:<hex6>`` where the hex suffix is the
        first 6 characters of the SHA-256 digest of the coordinate string.

        Returns
        -------
        str
            Canonical cohomology class label, or the stored value if
            ``cohomology_class`` is already non-empty.
        """
        if self.cohomology_class:
            return self.cohomology_class
        coord_hash = hashlib.sha256(self.coordinate.encode()).hexdigest()[:6]
        return f"{self.kind.cohomology_generator()}:{coord_hash}"

    def with_cohomology_class(self, cls: str) -> "BugReport":
        """Return a new BugReport with ``cohomology_class`` set to *cls*.

        Parameters
        ----------
        cls:
            The H¹ class label to assign.

        Returns
        -------
        BugReport
            Immutable copy with ``cohomology_class = cls``.
        """
        return replace(self, cohomology_class=cls)

    def with_trust_tier(self, tier: str) -> "BugReport":
        """Return a new BugReport with ``trust_tier`` promoted to *tier*.

        Promotion is only valid in the upward direction.  This method raises
        ``ValueError`` if *tier* is strictly weaker than the current tier
        (according to the TrustLevel integer ordering).

        Parameters
        ----------
        tier:
            The new trust tier name (e.g. ``"SOLVER_DISCHARGED"``).

        Returns
        -------
        BugReport
            Immutable copy with ``trust_tier = tier``.

        Raises
        ------
        ValueError
            If *tier* represents a downgrade in the trust ordering.
        """
        _order = {
            "CONTRADICTED": 0,
            "UNVERIFIED": 1,
            "ORACLE_PROPOSED": 2,
            "RUNTIME_WITNESSED": 3,
            "SOLVER_DISCHARGED": 4,
            "VERIFIED_PROOF": 5,
        }
        current = _order.get(self.trust_tier, 1)
        incoming = _order.get(tier, 1)
        if incoming < current:
            raise ValueError(
                f"Cannot demote trust tier from {self.trust_tier!r} to {tier!r}."
            )
        return replace(self, trust_tier=tier)

    def is_genuine(self) -> bool:
        """Return True iff this report represents a genuine obstruction.

        A report is genuine if it has a non-empty coordinate and a non-empty
        description.

        Returns
        -------
        bool
        """
        return bool(self.coordinate) and bool(self.description)

    def severity_score(self) -> int:
        """Return an integer severity score in [1, 5] for scheduling purposes.

        Derived from ``severity`` using a simple bucket scheme:
        [0.0, 0.2) → 1, [0.2, 0.4) → 2, [0.4, 0.6) → 3,
        [0.6, 0.8) → 4, [0.8, 1.0] → 5.

        Returns
        -------
        int
            An integer in [1, 5].
        """
        s = max(0.0, min(1.0, self.severity))
        return min(5, int(s * 5) + 1)

    # ------------------------------------------------------------------
    # JSON serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise this BugReport to a JSON-serialisable dict.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "bug_id": self.bug_id,
            "kind": self.kind.value,
            "coordinate": self.coordinate,
            "severity": self.severity,
            "description": self.description,
            "counterexample": self.counterexample,
            "trust_tier": self.trust_tier,
            "cohomology_class": self.cohomology_class or self.compute_cohomology_class(),
            "provenance": self.provenance,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BugReport":
        """Deserialise a BugReport from a plain-Python dict.

        Parameters
        ----------
        payload:
            A dict as produced by ``to_dict``.

        Returns
        -------
        BugReport
        """
        return cls(
            bug_id=payload.get("bug_id", uuid.uuid4().hex[:16]),
            kind=BugKind(payload.get("kind", BugKind.LOGIC_ERROR.value)),
            coordinate=payload.get("coordinate", ""),
            severity=float(payload.get("severity", 0.5)),
            description=payload.get("description", ""),
            counterexample=payload.get("counterexample"),
            trust_tier=payload.get("trust_tier", "ORACLE_PROPOSED"),
            cohomology_class=payload.get("cohomology_class", ""),
            provenance=dict(payload.get("provenance", {})),
            metadata=dict(payload.get("metadata", {})),
        )

    def __repr__(self) -> str:
        return (
            f"BugReport(bug_id={self.bug_id!r}, kind={self.kind.value!r}, "
            f"coordinate={self.coordinate!r}, severity={self.severity:.3f}, "
            f"trust_tier={self.trust_tier!r})"
        )


# ---------------------------------------------------------------------------
# BugDetectionResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BugDetectionResult:
    """Aggregate output of one bug-detection pass.

    A BugDetectionResult is produced by ``BugDetector.detect_bugs`` and
    represents the complete outcome of analysing a single source artefact.
    The ``bugs`` tuple is ordered by severity (descending) and contains at
    most one BugReport per distinct cohomology class.

    Immutability
    ------------
    BugDetectionResult is frozen to guarantee that the record is stable once
    produced.  Callers needing to refine the result should use ``replace()``
    from the dataclasses module.

    Status strings (Ch11 §11.3)
    ---------------------------
    ``"ok"``
        No obstructions were detected; the analysed artefact is locally
        section-compatible with the judgment sheaf.
    ``"bugs_found"``
        One or more obstructions were detected.
    ``"analysis_failed"``
        The analysis engine encountered an internal error and could not
        complete its pass.  The ``bugs`` tuple may be partial.
    ``"timeout"``
        The analysis exceeded its budget and was interrupted.

    Parameters
    ----------
    session_id:
        Identifier of the ``DetectionSession`` that produced this result.
    bugs:
        Tuple of ``BugReport`` objects, ordered by severity descending.
    status:
        One of ``"ok"``, ``"bugs_found"``, ``"analysis_failed"``,
        ``"timeout"``.
    witness:
        The symbolic counterexample that first triggered this detection run.
        May be ``None`` if status is ``"ok"``.
    elapsed_s:
        Wall-clock seconds consumed by the detection pass.
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    bugs: tuple[BugReport, ...] = ()
    status: str = "ok"
    witness: Any = None
    elapsed_s: float = 0.0

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def session(self) -> str:
        """Backward-compatible alias for the producing session identifier."""
        return self.session_id

    def by_kind(self, kind: BugKind) -> tuple[BugReport, ...]:
        """Return the subset of bugs with the given kind.

        Parameters
        ----------
        kind:
            The ``BugKind`` to filter on.

        Returns
        -------
        tuple[BugReport, ...]
        """
        return tuple(b for b in self.bugs if b.kind == kind)

    def most_severe(self) -> BugReport | None:
        """Return the BugReport with the highest severity, or None.

        Returns
        -------
        BugReport | None
        """
        if not self.bugs:
            return None
        return max(self.bugs, key=lambda b: b.severity)

    def cohomology_classes(self) -> frozenset[str]:
        """Return the set of distinct H¹ classes present in this result.

        Returns
        -------
        frozenset[str]
        """
        return frozenset(
            b.cohomology_class or b.compute_cohomology_class()
            for b in self.bugs
        )

    def has_trust_violation(self) -> bool:
        """Return True iff any bug has kind TRUST_VIOLATION.

        Returns
        -------
        bool
        """
        return any(b.kind == BugKind.TRUST_VIOLATION for b in self.bugs)

    def summary(self) -> dict[str, Any]:
        """Return a compact summary dict for logging and display.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "session_id": self.session_id,
            "status": self.status,
            "bug_count": len(self.bugs),
            "elapsed_s": round(self.elapsed_s, 4),
            "by_kind": {
                kind.value: len(self.by_kind(kind))
                for kind in BugKind
                if self.by_kind(kind)
            },
        }

    # ------------------------------------------------------------------
    # JSON serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise this BugDetectionResult to a JSON-serialisable dict.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "session_id": self.session_id,
            "bugs": [b.to_dict() for b in self.bugs],
            "status": self.status,
            "witness": self.witness,
            "elapsed_s": self.elapsed_s,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BugDetectionResult":
        """Deserialise a BugDetectionResult from a plain-Python dict.

        Parameters
        ----------
        payload:
            A dict as produced by ``to_dict``.

        Returns
        -------
        BugDetectionResult
        """
        return cls(
            session_id=payload.get("session_id", uuid.uuid4().hex[:16]),
            bugs=tuple(
                BugReport.from_dict(b)
                for b in payload.get("bugs", [])
            ),
            status=payload.get("status", "ok"),
            witness=payload.get("witness"),
            elapsed_s=float(payload.get("elapsed_s", 0.0)),
        )


# ---------------------------------------------------------------------------
# DetectionSession
# ---------------------------------------------------------------------------


@dataclass
class DetectionSession:
    """Monotone accumulator for BugReports across analysis iterations.

    DetectionSession is the *mutable* companion to ``BugDetectionResult``.
    It is updated in place as the analysis depth increases.  When analysis
    completes, ``finalise()`` produces an immutable ``BugDetectionResult``.

    Trust floor (theory2.tex §252)
    --------------------------------
    The ``trust_floor`` field constrains which evidence tiers are admitted as
    genuine obstructions.  Bugs whose evidence is weaker than ``trust_floor``
    are recorded as *candidate* obstructions but are not included in the
    finalised ``BugDetectionResult``.  This enforces the no-silent-trust-
    promotion principle at the session boundary.

    Valid trust_floor values (in ascending order):
    ``"CONTRADICTED"``, ``"UNVERIFIED"``, ``"ORACLE_PROPOSED"``,
    ``"RUNTIME_WITNESSED"``, ``"SOLVER_DISCHARGED"``, ``"VERIFIED_PROOF"``.

    Monotonicity invariant
    ----------------------
    The set of bugs_found is monotonically growing: bugs are added but never
    removed during a session.  This mirrors the accumulation semantics of the
    Ch11 debug loop.

    Parameters
    ----------
    session_id:
        Unique session identifier.
    target_path:
        Path or label of the analysed artefact.
    bugs_found:
        Mutable list of BugReports accumulated so far.
    analysis_depth:
        Integer depth counter; 0 = surface analysis, higher = deeper passes.
    trust_floor:
        Minimum trust tier admitted as a genuine obstruction.
    created_at:
        Datetime of session creation (UTC).
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    target_path: str = ""
    bugs_found: list[BugReport] = field(default_factory=list)
    analysis_depth: int = 0
    trust_floor: str = "ORACLE_PROPOSED"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    # ------------------------------------------------------------------
    # Trust ordering
    # ------------------------------------------------------------------

    _TRUST_ORDER: dict[str, int] = field(
        default_factory=lambda: {
            "CONTRADICTED": 0,
            "UNVERIFIED": 1,
            "ORACLE_PROPOSED": 2,
            "RUNTIME_WITNESSED": 3,
            "SOLVER_DISCHARGED": 4,
            "VERIFIED_PROOF": 5,
        },
        repr=False,
        compare=False,
    )

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_bug(self, bug: BugReport) -> None:
        """Append *bug* to bugs_found if its trust_tier meets the floor.

        If the bug's ``trust_tier`` is strictly weaker than ``trust_floor``
        it is silently dropped, enforcing the no-silent-trust-promotion
        principle.

        Parameters
        ----------
        bug:
            The ``BugReport`` to add.
        """
        order = self._TRUST_ORDER
        if order.get(bug.trust_tier, 0) >= order.get(self.trust_floor, 0):
            self.bugs_found.append(bug)

    def deepen(self) -> None:
        """Increment ``analysis_depth`` by one."""
        self.analysis_depth += 1

    def has_bug(self, bug_id: str) -> bool:
        """Return True iff a BugReport with ``bug_id`` is already recorded.

        Parameters
        ----------
        bug_id:
            The bug identifier to look up.

        Returns
        -------
        bool
        """
        return any(b.bug_id == bug_id for b in self.bugs_found)

    def bugs_by_kind(self, kind: BugKind) -> list[BugReport]:
        """Return all bugs with the given kind.

        Parameters
        ----------
        kind:
            The ``BugKind`` to filter on.

        Returns
        -------
        list[BugReport]
        """
        return [b for b in self.bugs_found if b.kind == kind]

    # ------------------------------------------------------------------
    # Finalisation
    # ------------------------------------------------------------------

    def finalise(self, elapsed_s: float = 0.0) -> BugDetectionResult:
        """Produce an immutable ``BugDetectionResult`` from this session.

        The bugs tuple is sorted by severity (descending) and deduplicated by
        cohomology_class so that the result contains at most one BugReport per
        distinct H¹ class.

        Parameters
        ----------
        elapsed_s:
            Wall-clock seconds consumed by the analysis.

        Returns
        -------
        BugDetectionResult
        """
        seen_classes: set[str] = set()
        unique_bugs: list[BugReport] = []
        for bug in sorted(self.bugs_found, key=lambda b: b.severity, reverse=True):
            cls = bug.cohomology_class or bug.compute_cohomology_class()
            if cls not in seen_classes:
                seen_classes.add(cls)
                unique_bugs.append(bug.with_cohomology_class(cls))
        status = "bugs_found" if unique_bugs else "ok"
        return BugDetectionResult(
            session_id=self.session_id,
            bugs=tuple(unique_bugs),
            status=status,
            elapsed_s=elapsed_s,
        )

    # ------------------------------------------------------------------
    # JSON serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise this session to a JSON-serialisable dict.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "session_id": self.session_id,
            "target_path": self.target_path,
            "bugs_found": [b.to_dict() for b in self.bugs_found],
            "analysis_depth": self.analysis_depth,
            "trust_floor": self.trust_floor,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DetectionSession":
        """Deserialise a DetectionSession from a plain-Python dict.

        Parameters
        ----------
        payload:
            A dict as produced by ``to_dict``.

        Returns
        -------
        DetectionSession
        """
        raw_dt = payload.get("created_at", "")
        try:
            created_at = datetime.fromisoformat(raw_dt)
        except (ValueError, TypeError):
            created_at = datetime.now(tz=timezone.utc)
        return cls(
            session_id=payload.get("session_id", uuid.uuid4().hex[:16]),
            target_path=payload.get("target_path", ""),
            bugs_found=[
                BugReport.from_dict(b) for b in payload.get("bugs_found", [])
            ],
            analysis_depth=int(payload.get("analysis_depth", 0)),
            trust_floor=payload.get("trust_floor", "ORACLE_PROPOSED"),
            created_at=created_at,
        )


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.encodings)
# ---------------------------------------------------------------------------

def bug_as_obstruction(bug: Any) -> dict[str, Any]:
    """Interpret a bug as a cohomology obstruction in H^1(U, D).

    Bugs ARE cohomological obstructions — they witness the failure of local
    sections to glue into a global section over the judgment-sheaf site.

    Parameters
    ----------
    bug : Any
        A BugReport or dict with at least ``coordinate`` and ``kind`` fields.

    Returns
    -------
    dict[str, Any]
        Obstruction record with ``class_label``, ``coordinate``, ``cocycle_data``,
        and ``descent_failure`` keys.
    """
    try:
        from jugeo.geometry.descent import compute_obstruction_class, DescentFailure
    except ImportError:
        compute_obstruction_class = None
        DescentFailure = None

    coord = getattr(bug, "coordinate", None) or (bug.get("coordinate") if isinstance(bug, dict) else None)
    kind = getattr(bug, "kind", None) or (bug.get("kind") if isinstance(bug, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind)

    obstruction: dict[str, Any] = {
        "coordinate": coord,
        "kind": kind_str,
        "class_label": f"H1_obstruction_{kind_str}",
        "cocycle_data": {"source": "bug_detection", "coordinate": coord},
        "descent_failure": None,
    }

    if compute_obstruction_class is not None:
        try:
            obs_class = compute_obstruction_class(coord, kind_str)
            obstruction["class_label"] = getattr(obs_class, "label", obstruction["class_label"])
            obstruction["cocycle_data"] = getattr(obs_class, "cocycle_data", obstruction["cocycle_data"])
        except Exception:
            pass

    if DescentFailure is not None:
        try:
            obstruction["descent_failure"] = DescentFailure(
                coordinate=coord, reason=f"bug_{kind_str}_blocks_gluing"
            )
        except Exception:
            pass

    return obstruction


def bug_evidence(bug: Any) -> dict[str, Any]:
    """Create negative evidence from a bug report.

    Bugs create negative evidence — they are witnesses AGAINST the claim
    that the section is well-formed at a given coordinate.

    Parameters
    ----------
    bug : Any
        A BugReport or dict with bug information.

    Returns
    -------
    dict[str, Any]
        Negative evidence record with ``polarity``, ``manifest_entry``,
        ``trust_impact``, and ``coordinate`` keys.
    """
    try:
        from jugeo.evidence.manifests import ManifestEntry, EvidencePolarity
    except ImportError:
        ManifestEntry = None
        EvidencePolarity = None

    coord = getattr(bug, "coordinate", None) or (bug.get("coordinate") if isinstance(bug, dict) else None)
    severity = getattr(bug, "severity", 0.5)
    if isinstance(bug, dict):
        severity = bug.get("severity", 0.5)

    evidence: dict[str, Any] = {
        "polarity": "NEGATIVE",
        "coordinate": coord,
        "trust_impact": -float(severity),
        "manifest_entry": None,
        "source": "bug_detection",
    }

    if EvidencePolarity is not None:
        try:
            evidence["polarity"] = EvidencePolarity.NEGATIVE
        except Exception:
            pass

    if ManifestEntry is not None:
        try:
            evidence["manifest_entry"] = ManifestEntry(
                coordinate=coord,
                polarity=evidence["polarity"],
                source="bug_detection",
            )
        except Exception:
            pass

    return evidence


def bug_encoding(bug: Any) -> dict[str, Any]:
    """Encode a bug as an SMT-encodable constraint.

    Bugs are SMT-encodable — each bug translates to a formula asserting
    that a particular section predicate fails at the bug's coordinate.

    Parameters
    ----------
    bug : Any
        A BugReport or dict with bug information.

    Returns
    -------
    dict[str, Any]
        Encoding record with ``formula``, ``variables``, ``coordinate``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings import encode_predicate, ScalarEncoding
    except ImportError:
        encode_predicate = None
        ScalarEncoding = None

    coord = getattr(bug, "coordinate", None) or (bug.get("coordinate") if isinstance(bug, dict) else None)
    kind = getattr(bug, "kind", None) or (bug.get("kind") if isinstance(bug, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind)

    encoding: dict[str, Any] = {
        "coordinate": coord,
        "encoding_kind": "bug_negation",
        "formula": f"(not (well_formed {coord} {kind_str}))",
        "variables": [f"wf_{coord}"],
        "scalar": None,
    }

    if encode_predicate is not None:
        try:
            enc = encode_predicate(coord, kind_str, negated=True)
            encoding["formula"] = getattr(enc, "formula", encoding["formula"])
            encoding["variables"] = getattr(enc, "variables", encoding["variables"])
        except Exception:
            pass

    if ScalarEncoding is not None:
        try:
            encoding["scalar"] = ScalarEncoding(
                coordinate=coord, value=0.0, label=f"bug_{kind_str}"
            )
        except Exception:
            pass

    return encoding


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "BugKind",
    "BugReport",
    "BugDetectionResult",
    "DetectionSession",
    "MANIFEST_SPEC_PROVENANCE",
    "JsonScalar",
    "JsonValue",
    "bug_as_obstruction",
    "bug_evidence",
    "bug_encoding",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json

    print("=== bug_detection/models.py smoke test ===")

    # Construct a BugReport
    report = BugReport(
        kind=BugKind.TYPE_ERROR,
        coordinate="src/example.py:42:4:Assign",
        severity=0.8,
        description="Variable 'x' annotated as int but assigned a str literal.",
        counterexample={"line": 42, "expected": "int", "got": "str"},
        trust_tier="ORACLE_PROPOSED",
        provenance={"analyser": "ast_bridge", "pass": 1},
    )
    report = report.with_cohomology_class(report.compute_cohomology_class())
    print("BugReport:", repr(report))
    print("Judgment tuple:", report.judgment_tuple)
    print("Severity score:", report.severity_score())

    # Round-trip serialisation
    d = report.to_dict()
    report2 = BugReport.from_dict(d)
    assert report2.bug_id == report.bug_id
    assert report2.kind == report.kind
    print("Round-trip OK:", report2.bug_id)

    # Session accumulation
    session = DetectionSession(target_path="src/example.py")
    session.add_bug(report)
    session.deepen()
    result = session.finalise(elapsed_s=0.123)
    print("Result:", result.summary())
    assert result.status == "bugs_found"
    assert len(result.bugs) == 1

    # Result round-trip
    rd = result.to_dict()
    result2 = BugDetectionResult.from_dict(rd)
    assert result2.session_id == result.session_id
    print("BugDetectionResult round-trip OK")
    print("=== smoke test PASSED ===")
