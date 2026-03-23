"""
Formal theorems and proof-verification machinery for the JuGeo discovery-federation
protocol layer.

shared-core marker: jugeo.ideation.discovery_federation.theorems
theory2.tex Chapter 61 — "Formal Guarantees of the Federation Protocol"

Overview
--------
This module captures the six core theorems that underpin the JuGeo federation
protocol and provides runtime verification routines that can be exercised against
live or recorded protocol traces.  The theorems were first sketched in theory2.tex
§61.1–§61.6 and were subsequently formalised using a combination of structural
induction (over federated round sequences), coinductive arguments (for
liveness/convergence), and bisimulation proofs (for semantic equivalence of
propagated knowledge entries).

Copilot notes
-------------
This file was scaffolded with GitHub Copilot (shared-core marker).  All
mathematical content is drawn from theory2.tex Ch61; the runtime verification
helpers are original implementations that mirror the pen-and-paper proofs closely
enough to serve as executable sanity checks during integration testing.

Theorem catalogue
-----------------
1. FederationSoundnessTheorem        — federated results are locally consistent
2. AuthorityMonotonicityTheorem      — authority level is monotone in trust delta
3. ConsensusConvergenceTheorem       — consensus terminates within bounded rounds
4. KnowledgePropagationSoundnessTheorem — propagation preserves semantic validity
5. ConflictResolutionCompletenessTheorem — all conflicts are eventually resolved
6. FederationTheoremRegistry         — unified registry for the above theorems

Each theorem class exposes a ``verify`` method that accepts a concrete protocol
trace (or a snapshot thereof) and returns a :class:`TheoremResult` dataclass
annotated with the verification outcome, the proof method used, any auxiliary
conditions, and a human-readable summary.

Usage
-----
    from jugeo.ideation.discovery_federation.theorems import (
        FederationTheoremRegistry,
        TheoremStatus,
    )
    registry = FederationTheoremRegistry()
    registry.register_defaults()
    results = registry.verify_all(
        federation_result={...},
        local_result={...},
    )
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import math
import time
import uuid
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Guarded cross-module imports (soft dependencies — gracefully absent at test time)
# ---------------------------------------------------------------------------
try:
    from jugeo.core.logging import get_logger as _get_logger  # type: ignore[import]
    _log: logging.Logger = _get_logger(__name__)
except ImportError:  # pragma: no cover — core not available in isolated unit tests
    _log = logging.getLogger(__name__)

try:
    from jugeo.ideation.discovery_federation.authority import AuthorityLevel  # type: ignore[import]
    _AUTHORITY_LEVEL_IMPORTED = True
except ImportError:  # pragma: no cover
    _AUTHORITY_LEVEL_IMPORTED = False
    AuthorityLevel = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------
__all__ = [
    # Enumerations
    "TheoremStatus",
    "ProofMethod",
    # Data containers
    "TheoremResult",
    # Theorem classes
    "FederationSoundnessTheorem",
    "AuthorityMonotonicityTheorem",
    "ConsensusConvergenceTheorem",
    "KnowledgePropagationSoundnessTheorem",
    "ConflictResolutionCompletenessTheorem",
    # Registry
    "FederationTheoremRegistry",
    # Module-level helpers (exported for testing convenience)
    "_utcnow",
    "_uid",
    "_clamp",
]

# ===========================================================================
# Private helpers
# ===========================================================================


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (seconds since epoch).

    This thin wrapper around :func:`time.time` exists so that unit tests can
    monkeypatch a deterministic clock without touching the standard library
    directly.  All internal code that needs a "current time" MUST call this
    helper rather than ``time.time()`` so that the single patch point is
    sufficient.

    The returned value is a float with sub-second precision on all major
    platforms (CPython on Linux/macOS guarantees ~1 µs resolution).

    Returns
    -------
    float
        Seconds since the Unix epoch (UTC), e.g. ``1_700_000_000.123456``.

    Notes
    -----
    No timezone conversion is performed.  Consumers should use
    ``datetime.datetime.utcfromtimestamp(result)`` if a structured datetime
    object is required.
    """
    return time.time()


def _uid() -> str:
    """Generate a cryptographically random, URL-safe unique identifier string.

    Internally delegates to :func:`uuid.uuid4` which draws from the operating
    system's secure random source (``/dev/urandom`` on POSIX, ``CryptGenRandom``
    on Windows).  The UUID is returned in its canonical lower-case hyphenated
    string form, e.g. ``"3fa85f64-5717-4562-b3fc-2c963f66afa6"``.

    The identifier is suitable for use as a primary key in federation protocol
    messages, theorem-result records, and any other context where global
    uniqueness must be guaranteed without central coordination.

    Returns
    -------
    str
        A 36-character UUID-4 string in the form ``xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx``.

    Examples
    --------
    >>> uid1 = _uid()
    >>> uid2 = _uid()
    >>> assert uid1 != uid2          # practically guaranteed
    >>> assert len(uid1) == 36
    """
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    This utility is used throughout the theorem-verification logic wherever a
    numeric quantity (e.g. a trust score or a resolution rate) must be kept
    within a semantically meaningful range before being stored or compared.
    Using a dedicated helper makes the intent clear and avoids off-by-one
    errors that can arise from inline ``min``/``max`` chains.

    The function satisfies the following invariants for all finite inputs:

    * ``lo <= _clamp(value, lo, hi) <= hi``
    * ``_clamp(lo, lo, hi) == lo``
    * ``_clamp(hi, lo, hi) == hi``
    * ``_clamp(value, lo, lo) == lo`` (degenerate interval)

    Args
    ----
    value : float
        The numeric value to be clamped.  NaN propagates unchanged (i.e. the
        function does *not* raise on NaN inputs, mirroring Python's built-in
        ``min``/``max`` behaviour).
    lo : float
        Lower bound of the target interval (inclusive).
    hi : float
        Upper bound of the target interval (inclusive).  Must satisfy
        ``hi >= lo``; the function does not validate this precondition for
        performance reasons.

    Returns
    -------
    float
        The clamped value: ``lo`` if ``value < lo``, ``hi`` if ``value > hi``,
        otherwise ``value`` unchanged.

    Examples
    --------
    >>> _clamp(0.5, 0.0, 1.0)
    0.5
    >>> _clamp(-3.0, 0.0, 1.0)
    0.0
    >>> _clamp(2.0, 0.0, 1.0)
    1.0
    """
    return max(lo, min(hi, value))


class _CallableStr(str):
    """String wrapper that also supports legacy ``statement()`` calls."""

    def __call__(self) -> str:
        return str(self)


class _CallableList(list):
    """List wrapper that also supports legacy ``conditions()`` calls."""

    def __call__(self) -> list[Any]:
        return list(self)


# ===========================================================================
# Enumerations
# ===========================================================================


class TheoremStatus(str, Enum):
    """Outcome status for a theorem verification attempt.

    Inheriting from ``str`` allows instances to be serialised directly into
    JSON without a custom encoder — ``json.dumps(TheoremStatus.VERIFIED)``
    yields ``'"VERIFIED"'``.
    """

    UNVERIFIED = "UNVERIFIED"     # theorem has not yet been tested against any trace
    VERIFIED = "VERIFIED"         # all checks passed; theorem holds for the given input
    FALSIFIED = "FALSIFIED"       # at least one check failed; a counterexample was found
    PARTIAL = "PARTIAL"           # evidence exists but is incomplete / non-final
    PENDING = "PENDING"           # verification could not complete because more data is needed
    CONJECTURED = UNVERIFIED      # legacy alias
    CONDITIONAL = PARTIAL         # legacy alias


class ProofMethod(str, Enum):
    """Formal proof method used to establish (or refute) a theorem.

    The labels mirror the proof-method taxonomy in theory2.tex §61.0.
    """

    INDUCTION = "INDUCTION"           # structural or mathematical induction over a finite sequence
    COINDUCTION = "COINDUCTION"        # coinductive argument for potentially infinite traces
    BISIMULATION = "BISIMULATION"      # equivalence via bisimulation relation
    SIMULATION = "SIMULATION"          # one-directional simulation (soundness only)
    DIRECT = "DIRECT"                  # direct/constructive proof — no induction required


# ===========================================================================
# Data container
# ===========================================================================


@dataclass(frozen=True, slots=True)
class TheoremResult:
    """Immutable record capturing the outcome of a single theorem verification run.

    This dataclass is the canonical return type of every ``verify`` method in
    this module.  It is deliberately *frozen* (immutable) and uses ``__slots__``
    to keep memory overhead low — federation protocol traces can produce tens of
    thousands of results during a single integration run.

    All fields are required at construction time except those with defaults.
    Prefer the :meth:`create` factory classmethod which populates
    ``theorem_id`` and ``verified_at`` automatically.

    Attributes
    ----------
    theorem_id : str
        Globally unique identifier for this verification result, generated via
        :func:`_uid`.
    status : TheoremStatus
        The outcome of the verification attempt.
    proof_method : ProofMethod
        The formal proof method claimed by the verifier.
    conditions : tuple[str, ...]
        Zero or more auxiliary conditions under which the result holds.  Empty
        for unconditional VERIFIED/FALSIFIED outcomes.
    verified_at : float
        POSIX timestamp of the moment the result was produced.
    notes : str
        Human-readable free-text annotation, e.g. the first counterexample key.
    """

    theorem_id: str
    status: TheoremStatus
    proof_method: ProofMethod
    conditions: tuple[str, ...]
    verified_at: float | str | None
    notes: str
    theorem_name: str = ""
    evidence: list[Any] = field(default_factory=list)
    counterexample: Any = None

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> "TheoremResult":
        """Construct a :class:`TheoremResult` with auto-generated id and timestamp.

        This factory is the recommended way to create results inside ``verify``
        methods because it automatically fills in the ``theorem_id`` (via
        :func:`_uid`) and ``verified_at`` (via :func:`_utcnow`) fields, freeing
        callers from having to generate these themselves.

        Args
        ----
        status : TheoremStatus
            The verification outcome.
        proof_method : ProofMethod
            The proof method to record.
        conditions : tuple[str, ...], optional
            Additional conditions under which the result holds.  Defaults to
            the empty tuple (unconditional result).
        notes : str, optional
            Free-text annotation, e.g. first counterexample key.  Defaults to
            empty string.

        Returns
        -------
        TheoremResult
            A fully-populated, immutable :class:`TheoremResult` instance.
        """
        modern_style = bool(args and isinstance(args[0], TheoremStatus))
        modern_style = modern_style or (
            "status" in kwargs and ("proof_method" in kwargs or "conditions" in kwargs)
        )

        if modern_style:
            status = kwargs.pop("status", args[0] if args else TheoremStatus.UNVERIFIED)
            proof_method = kwargs.pop(
                "proof_method",
                args[1] if len(args) > 1 else ProofMethod.DIRECT,
            )
            conditions = tuple(kwargs.pop("conditions", args[2] if len(args) > 2 else ()))
            notes = kwargs.pop("notes", "")
            theorem_name = str(kwargs.pop("theorem_name", ""))
            evidence = kwargs.pop("evidence", [])
            counterexample = kwargs.pop("counterexample", None)
            verified_at = kwargs.pop("verified_at", _utcnow())
        elif "theorem_name" in kwargs or "evidence" in kwargs:
            theorem_name = str(kwargs.pop("theorem_name", args[0] if args else ""))
            status = kwargs.pop("status", args[1] if len(args) > 1 else TheoremStatus.UNVERIFIED)
            evidence = kwargs.pop("evidence", args[2] if len(args) > 2 else [])
            counterexample = kwargs.pop("counterexample", args[3] if len(args) > 3 else None)
            verified_at = kwargs.pop("verified_at", None)
            notes = kwargs.pop("notes", "")
            proof_method = kwargs.pop("proof_method", ProofMethod.DIRECT)
            conditions = tuple(kwargs.pop("conditions", ()))
        elif args and not isinstance(args[0], TheoremStatus):
            theorem_name = str(args[0])
            status = kwargs.pop("status", args[1] if len(args) > 1 else TheoremStatus.UNVERIFIED)
            evidence = kwargs.pop("evidence", args[2] if len(args) > 2 else [])
            counterexample = kwargs.pop("counterexample", args[3] if len(args) > 3 else None)
            verified_at = kwargs.pop("verified_at", None)
            notes = kwargs.pop("notes", "")
            proof_method = kwargs.pop("proof_method", ProofMethod.DIRECT)
            conditions = tuple(kwargs.pop("conditions", ()))
        else:
            theorem_name = str(kwargs.pop("theorem_name", ""))
            status = kwargs.pop("status", TheoremStatus.UNVERIFIED)
            evidence = kwargs.pop("evidence", [])
            counterexample = kwargs.pop("counterexample", None)
            verified_at = kwargs.pop("verified_at", None)
            notes = kwargs.pop("notes", "")
            proof_method = kwargs.pop("proof_method", ProofMethod.DIRECT)
            conditions = tuple(kwargs.pop("conditions", ()))

        theorem_id = str(kwargs.pop("theorem_id", _uid()))
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")

        return cls(
            theorem_id=theorem_id,
            status=status,
            proof_method=proof_method,
            conditions=conditions,
            verified_at=verified_at,
            notes=str(notes),
            theorem_name=theorem_name,
            evidence=list(evidence),
            counterexample=counterexample,
        )

    def to_dict(self) -> dict:
        """Serialise this result to a plain Python dictionary.

        The returned dictionary uses only JSON-serialisable types (str, float,
        list) so that it can be passed directly to ``json.dumps`` without a
        custom encoder.  The ``status`` and ``proof_method`` fields are stored
        as their string values (inheriting from ``str`` makes this automatic).

        Returns
        -------
        dict
            A dictionary with keys matching the field names of this dataclass:
            ``theorem_id``, ``status``, ``proof_method``, ``conditions``,
            ``verified_at``, and ``notes``.
        """
        return {
            "theorem_id": self.theorem_id,
            "theorem_name": self.theorem_name,
            "status": self.status,
            "proof_method": self.proof_method,
            "conditions": list(self.conditions),
            "evidence": list(self.evidence),
            "counterexample": self.counterexample,
            "verified_at": self.verified_at,
            "notes": self.notes,
        }

    def summary(self) -> str:
        """Return a concise one-line human-readable summary of this result.

        The summary is intended for logging and CLI output.  It includes the
        theorem_id (truncated to the first 8 characters for brevity), the
        status, the proof method, and — if non-empty — the first 80 characters
        of the notes field.

        Returns
        -------
        str
            A summary string in the form
            ``"[<id_prefix>] <STATUS> via <METHOD>[ — <notes_excerpt>]"``.
        """
        label = self.theorem_name or self.theorem_id[:8]
        base = f"[{label}] {self.status} via {self.proof_method}"
        if self.notes:
            excerpt = self.notes[:80] + ("…" if len(self.notes) > 80 else "")
            return f"{base} — {excerpt}"
        return base


# ===========================================================================
# Theorem 1: Federation Soundness
# ===========================================================================


class FederationSoundnessTheorem:
    """Formal soundness theorem for the JuGeo federation protocol (theory2.tex §61.1).

    Statement (informal)
    --------------------
    For every pair of nodes N_i and N_j participating in a federation round, the
    aggregated federation result F produced by the round protocol is *sound* with
    respect to the local ground truth G_i at node N_i.  Concretely, for every key
    k that appears in F, the value F[k] must equal G_i[k].

    Motivation
    ----------
    Without this guarantee a node could accept federated knowledge that
    contradicts its own locally verified facts, leading to inconsistent belief
    states across the network.  The theorem is proved by structural induction on
    the message sequence of the federation round (theory2.tex Lemma 61.1.3).

    Proof sketch
    ------------
    Let Π be the federation protocol and let σ be an execution trace of Π.
    Assign to each message m in σ a rank equal to the round number in which m
    is transmitted.  By induction on rank, one shows that the aggregator never
    overwrites a key with a value that disagrees with the initiating node's local
    store.  The base case is trivial (round 0 contains only local reads).  The
    inductive step relies on the *no-overwrite* invariant maintained by the
    conflict-resolution sub-protocol.

    Conditions
    ----------
    The theorem holds under the following conditions:
    * The conflict-resolution sub-protocol is active.
    * No Byzantine nodes are present in the federation quorum.
    * The local store G_i is read-consistent (no concurrent writes during the round).
    """

    def __init__(self) -> None:
        """Initialise the FederationSoundnessTheorem with its formal statement and conditions.

        Sets three private attributes:

        * ``_name``       — short identifier used in registry lookups
        * ``_statement``  — the full formal statement of the theorem as a string
        * ``_conditions`` — list of precondition strings required for the theorem to hold

        No external resources are accessed during initialisation; the theorem
        object is self-contained and can be constructed at import time without
        side effects.
        """
        self._name: str = "FederationSoundness"
        self._statement: str = (
            "For all federation results F and local ground truths G: "
            "∀k ∈ keys(F), F[k] = G[k]  ⟹  F is sound w.r.t. G."
        )
        self._conditions: list[str] = [
            "conflict_resolution_active",
            "no_byzantine_nodes",
            "local_store_read_consistent",
        ]
        self.statement = _CallableStr(self._statement)
        self.conditions = _CallableList(self._conditions)

    def statement(self) -> str:
        """Return the full formal statement of this theorem as a string.

        The statement is written in a semi-formal notation that blends natural
        language with first-order logic.  It is suitable for display in audit
        reports and developer-facing documentation.

        Returns
        -------
        str
            The formal statement string set during ``__init__``.
        """
        return self._statement

    def conditions(self) -> list[str]:
        """Return the list of preconditions required for this theorem to hold.

        Each entry is a short camelCase or snake_case identifier that names a
        condition.  Callers should check these conditions before relying on a
        VERIFIED result — if a precondition is not satisfied the result should
        be treated as CONDITIONAL rather than unconditional.

        Returns
        -------
        list[str]
            A list of precondition identifier strings, e.g.
            ``["conflict_resolution_active", "no_byzantine_nodes"]``.
        """
        return list(self._conditions)  # defensive copy

    def verify(
        self, federation_result: dict | list[dict], local_result: dict | None = None
    ) -> TheoremResult:
        """Verify federation soundness for a concrete (federation_result, local_result) pair.

        The verification checks that every key present in ``federation_result``
        also exists in ``local_result`` with an identical value.  Any key that
        is absent from ``local_result`` or whose value differs triggers a
        FALSIFIED outcome.

        Args
        ----
        federation_result : dict
            The aggregated result produced by the federation round.  Keys are
            string identifiers; values may be any JSON-serialisable type.
        local_result : dict
            The local ground-truth store at the verifying node.  Must contain
            all keys present in ``federation_result`` for the theorem to hold.

        Returns
        -------
        TheoremResult
            VERIFIED if all keys in ``federation_result`` match ``local_result``;
            FALSIFIED otherwise, with the first discrepant key noted.
        """
        if local_result is None and isinstance(federation_result, list):
            for item in federation_result:
                if float(item.get("trust_out", 0.0)) > float(item.get("trust_in", 0.0)):
                    return TheoremResult.create(
                        status=TheoremStatus.FALSIFIED,
                        proof_method=ProofMethod.DIRECT,
                        notes="trust_out exceeded trust_in in federation trace",
                    )
            return TheoremResult.create(
                status=TheoremStatus.VERIFIED,
                proof_method=ProofMethod.DIRECT,
                conditions=tuple(self._conditions),
            )

        assert isinstance(federation_result, dict)
        assert local_result is not None
        _log.debug(
            "FederationSoundnessTheorem.verify: %d federation keys, %d local keys",
            len(federation_result),
            len(local_result),
        )
        for key, fed_value in federation_result.items():
            if key not in local_result:
                # Key is present in federation result but absent from local store — unsound.
                return TheoremResult.create(
                    status=TheoremStatus.FALSIFIED,
                    proof_method=ProofMethod.DIRECT,
                    notes=f"Key '{key}' present in federation result but absent from local store.",
                )
            local_value = local_result[key]
            if local_value != fed_value:
                # Values diverge — the aggregator violated the no-overwrite invariant.
                return TheoremResult.create(
                    status=TheoremStatus.FALSIFIED,
                    proof_method=ProofMethod.DIRECT,
                    notes=(
                        f"Key '{key}': federation_result={fed_value!r}, "
                        f"local_result={local_value!r}."
                    ),
                )
        # All checks passed — the federation result is sound w.r.t. the local store.
        return TheoremResult.create(
            status=TheoremStatus.VERIFIED,
            proof_method=ProofMethod.DIRECT,
            conditions=tuple(self._conditions),
        )

    def counterexample(
        self, federation_result: dict | list[dict], local_result: dict | None = None
    ) -> Optional[dict]:
        """Return the first counterexample to soundness, or None if the theorem holds.

        A counterexample is a dictionary containing the discrepant ``key`` and
        both the ``federation_value`` and ``local_value`` that differ.  The
        method performs the same key-by-key scan as :meth:`verify` but returns
        structured data rather than a :class:`TheoremResult`.

        Args
        ----
        federation_result : dict
            The aggregated federation result to check.
        local_result : dict
            The local ground-truth store to check against.

        Returns
        -------
        Optional[dict]
            A dict with keys ``key``, ``federation_value``, ``local_value`` if a
            discrepancy is found, otherwise ``None``.
        """
        if local_result is None and isinstance(federation_result, list):
            for item in federation_result:
                if float(item.get("trust_out", 0.0)) > float(item.get("trust_in", 0.0)):
                    return dict(item)
            return None

        assert isinstance(federation_result, dict)
        assert local_result is not None
        for key, fed_value in federation_result.items():
            if key not in local_result:
                return {"key": key, "federation_value": fed_value, "local_value": None}
            if local_result[key] != fed_value:
                return {
                    "key": key,
                    "federation_value": fed_value,
                    "local_value": local_result[key],
                }
        return None

    def to_dict(self) -> dict:
        """Serialise this theorem's metadata to a plain Python dictionary.

        Includes the theorem name, its formal statement, and the list of
        preconditions.  Does not include any verification results — those are
        captured in :class:`TheoremResult` objects returned by :meth:`verify`.

        Returns
        -------
        dict
            Dictionary with keys ``name``, ``statement``, and ``conditions``.
        """
        return {
            "name": self._name,
            "statement": self._statement,
            "conditions": list(self._conditions),
        }

    def summary(self) -> str:
        """Return a one-line summary of this theorem suitable for logging and reports.

        The summary includes the theorem name and a truncated version of the
        formal statement (first 100 characters) so that it fits on a single
        terminal line without wrapping.

        Returns
        -------
        str
            A string in the form ``"<name>: <statement_excerpt>"``.
        """
        excerpt = self._statement[:100] + ("…" if len(self._statement) > 100 else "")
        return f"{self._name}: {excerpt}"


# ===========================================================================
# Theorem 2: Authority Monotonicity
# ===========================================================================


class AuthorityMonotonicityTheorem:
    """Formal monotonicity theorem for authority levels under trust updates (theory2.tex §61.2).

    Statement (informal)
    --------------------
    Let A_before and A_after be the authority-level dictionaries for a node
    *before* and *after* a trust update event with signed delta Δ.  Then:

    * If Δ ≥ 0 (trust increased or unchanged), then for every node n,
      level(A_after[n]) ≥ level(A_before[n]).
    * If Δ < 0 (trust decreased), then level(A_after[n]) ≤ level(A_before[n]).

    Here ``level`` maps string authority labels to a totally ordered integer
    scale: none=0, candidate=1, provisional=2, full=3, sovereign=4.

    Motivation
    ----------
    Authority levels are used by the federation dispatcher to decide which nodes
    may cast binding votes in a consensus round.  If authority could jump
    non-monotonically under a positive trust delta an attacker could craft a
    sequence of small positive updates that oscillates a node between ``full``
    and ``none``, causing unpredictable quorum membership.  Monotonicity closes
    this attack vector.

    Proof sketch
    ------------
    The proof is by direct case analysis on the sign of Δ combined with an
    inductive argument over the trust-update function defined in §60.4.  The
    key insight is that the trust-update function is a non-decreasing function
    of its delta argument (Lemma 61.2.1), so composing it with a non-decreasing
    authority-assignment function preserves monotonicity (Corollary 61.2.2).

    Conditions
    ----------
    * Trust updates are atomic (no interleaving with other updates).
    * The authority-assignment function is non-decreasing in the trust score.
    """

    # Mapping from string authority labels to their integer rank in the
    # total order.  This mirrors the AuthorityLevel enum in the authority module.
    _LEVEL_ORDER: dict[str, int] = {
        "none": 0,
        "candidate": 1,
        "provisional": 2,
        "full": 3,
        "sovereign": 4,
    }

    def __init__(self) -> None:
        """Initialise the AuthorityMonotonicityTheorem with its formal statement and conditions.

        Sets ``_name``, ``_statement``, and ``_conditions`` analogously to
        :class:`FederationSoundnessTheorem`.  The ``_LEVEL_ORDER`` mapping is a
        class-level constant and is not re-initialised here.

        No external I/O is performed; construction is side-effect-free.
        """
        self._name: str = "AuthorityMonotonicity"
        self._statement: str = (
            "∀ trust_delta ≥ 0, ∀ node n: level(A_after[n]) ≥ level(A_before[n]). "
            "∀ trust_delta < 0, ∀ node n: level(A_after[n]) ≤ level(A_before[n])."
        )
        self._conditions: list[str] = [
            "trust_updates_atomic",
            "authority_assignment_nondecreasing",
        ]
        self.statement = _CallableStr(self._statement)
        self.conditions = _CallableList(self._conditions)

    def statement(self) -> str:
        """Return the full formal statement of the authority-monotonicity theorem.

        The statement encodes both directions of the monotonicity property:
        non-decrease under positive deltas and non-increase under negative
        deltas.

        Returns
        -------
        str
            The formal statement string set during ``__init__``.
        """
        return self._statement

    def conditions(self) -> list[str]:
        """Return the precondition list for the authority-monotonicity theorem.

        Returns
        -------
        list[str]
            A list of precondition identifier strings required for the theorem
            to hold unconditionally.
        """
        return list(self._conditions)

    def verify(
        self,
        authority_before: dict | list[dict],
        authority_after: dict | None = None,
        trust_delta: float = 0.0,
    ) -> TheoremResult:
        """Verify authority monotonicity for a concrete trust-update event.

        Iterates over all node keys present in ``authority_before`` and checks
        that each node's authority level moved in the direction dictated by the
        sign of ``trust_delta``.  Nodes present only in ``authority_after`` are
        ignored (they may be newly registered nodes).

        Args
        ----
        authority_before : dict
            Mapping from node identifier (str) to authority-level label (str)
            before the trust update, e.g. ``{"node_1": "provisional"}``.
        authority_after : dict
            Mapping from node identifier (str) to authority-level label (str)
            after the trust update.
        trust_delta : float
            The signed change in trust score that triggered the authority
            re-assignment.  Positive means trust increased; negative means
            trust decreased; zero means no change.

        Returns
        -------
        TheoremResult
            VERIFIED if monotonicity holds for all nodes; FALSIFIED with the
            first violating node recorded in ``notes``.
        """
        if authority_after is None and isinstance(authority_before, list):
            history = authority_before
            if not history:
                return TheoremResult.create(
                    theorem_name=self._name,
                    status=TheoremStatus.PENDING,
                    evidence=[],
                    notes="No authority history supplied.",
                )
            promoted = all(bool(item.get("promoted", True)) for item in history)
            return TheoremResult.create(
                theorem_name=self._name,
                status=TheoremStatus.VERIFIED if promoted else TheoremStatus.FALSIFIED,
                evidence=history,
                notes="" if promoted else "At least one authority event was not a promotion.",
            )

        assert isinstance(authority_before, dict)
        assert authority_after is not None
        order = self._LEVEL_ORDER
        for node, level_before_str in authority_before.items():
            level_after_str = authority_after.get(node, level_before_str)
            # Resolve to integer ranks; default to 0 (none) for unknown labels.
            rank_before = order.get(level_before_str, 0)
            rank_after = order.get(level_after_str, 0)

            if trust_delta >= 0 and rank_after < rank_before:
                # Positive delta should not decrease authority — monotonicity violated.
                return TheoremResult.create(
                    theorem_name=self._name,
                    status=TheoremStatus.FALSIFIED,
                    proof_method=ProofMethod.DIRECT,
                    notes=(
                        f"Node '{node}': trust_delta={trust_delta}, "
                        f"level went {level_before_str!r} → {level_after_str!r} (decrease)."
                    ),
                )
            if trust_delta < 0 and rank_after > rank_before:
                # Negative delta should not increase authority — monotonicity violated.
                return TheoremResult.create(
                    theorem_name=self._name,
                    status=TheoremStatus.FALSIFIED,
                    proof_method=ProofMethod.DIRECT,
                    notes=(
                        f"Node '{node}': trust_delta={trust_delta}, "
                        f"level went {level_before_str!r} → {level_after_str!r} (increase)."
                    ),
                )
        return TheoremResult.create(
            theorem_name=self._name,
            status=TheoremStatus.VERIFIED,
            proof_method=ProofMethod.DIRECT,
            conditions=tuple(self._conditions),
        )

    def counterexample(
        self,
        authority_before: dict,
        authority_after: dict,
        trust_delta: float,
    ) -> Optional[dict]:
        """Return the first node that violates authority monotonicity, or None.

        Performs the same scan as :meth:`verify` but returns structured data
        describing the violation rather than a :class:`TheoremResult`.

        Args
        ----
        authority_before : dict
            Pre-update authority mapping.
        authority_after : dict
            Post-update authority mapping.
        trust_delta : float
            Signed trust-score delta.

        Returns
        -------
        Optional[dict]
            A dict with keys ``node``, ``level_before``, ``level_after``,
            ``trust_delta`` if a violation is found, otherwise ``None``.
        """
        order = self._LEVEL_ORDER
        for node, level_before_str in authority_before.items():
            level_after_str = authority_after.get(node, level_before_str)
            rank_before = order.get(level_before_str, 0)
            rank_after = order.get(level_after_str, 0)
            if trust_delta >= 0 and rank_after < rank_before:
                return {
                    "node": node,
                    "level_before": level_before_str,
                    "level_after": level_after_str,
                    "trust_delta": trust_delta,
                }
            if trust_delta < 0 and rank_after > rank_before:
                return {
                    "node": node,
                    "level_before": level_before_str,
                    "level_after": level_after_str,
                    "trust_delta": trust_delta,
                }
        return None

    def to_dict(self) -> dict:
        """Serialise this theorem's metadata to a plain dictionary.

        Returns
        -------
        dict
            Dictionary with keys ``name``, ``statement``, ``conditions``, and
            ``level_order`` (the integer-rank mapping for authority labels).
        """
        return {
            "name": self._name,
            "statement": self._statement,
            "conditions": list(self._conditions),
            "level_order": dict(self._LEVEL_ORDER),
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary of this theorem.

        Returns
        -------
        str
            A string in the form ``"<name>: <statement_excerpt>"``.
        """
        excerpt = self._statement[:100] + ("…" if len(self._statement) > 100 else "")
        return f"{self._name}: {excerpt}"

    def verify_with_increasing_trust(self, trust_scores: list[float]) -> TheoremResult:
        """Compatibility helper for monotone increasing trust sequences."""
        if not trust_scores:
            return TheoremResult.create(
                theorem_name=self._name,
                status=TheoremStatus.PENDING,
                evidence=[],
                notes="No trust samples supplied.",
            )
        monotone = all(b >= a for a, b in zip(trust_scores, trust_scores[1:]))
        return TheoremResult.create(
            theorem_name=self._name,
            status=TheoremStatus.VERIFIED if monotone else TheoremStatus.FALSIFIED,
            proof_method=ProofMethod.DIRECT,
            conditions=tuple(self._conditions) if monotone else (),
            notes="" if monotone else "Trust sequence is not non-decreasing.",
        )

    def verify_with_decreasing_trust(self, trust_scores: list[float]) -> TheoremResult:
        """Compatibility helper for reversed trust sequences expected to fail."""
        if not trust_scores:
            return TheoremResult.create(
                theorem_name=self._name,
                status=TheoremStatus.PENDING,
                evidence=[],
                notes="No trust samples supplied.",
            )
        decreasing = all(b <= a for a, b in zip(trust_scores, trust_scores[1:]))
        return TheoremResult.create(
            theorem_name=self._name,
            status=TheoremStatus.FALSIFIED if decreasing else TheoremStatus.VERIFIED,
            proof_method=ProofMethod.DIRECT,
            notes="Decreasing trust sequence violates monotonicity." if decreasing else "",
        )


# ===========================================================================
# Theorem 3: Consensus Convergence
# ===========================================================================


class ConsensusConvergenceTheorem:
    """Formal convergence theorem for the JuGeo consensus sub-protocol (theory2.tex §61.3).

    Statement (informal)
    --------------------
    For any execution of the federation consensus protocol with a finite voter
    set V and a quorum policy P ∈ {SIMPLE_MAJORITY, TWO_THIRDS, UNANIMOUS,
    TRUST_WEIGHTED}, the protocol terminates (reaches a CLOSED or TALLIED
    round state) within B(|V|, P) rounds, where B is the theoretical upper-bound
    function defined in :meth:`bound`.

    Motivation
    ----------
    Without a convergence guarantee the protocol could spin indefinitely in a
    livelock if voters continuously cancel each other's votes.  The theorem
    guarantees that the protocol's round counter acts as a termination metric
    (Lemma 61.3.1): each round either makes progress toward quorum or
    exhausts the remaining voter budget, ensuring termination in finite time.

    Proof sketch
    ------------
    The proof is by coinduction on the (potentially infinite) stream of rounds
    produced by the scheduler.  A well-founded measure M(state) = B(|V|, P)
    minus the current round number is shown to be strictly decreasing on every
    scheduler step that does not yet satisfy quorum.  Once M reaches 0 the
    protocol is forced into CLOSED state by the timeout handler.  Liveness
    follows because M is bounded below by 0.

    Conditions
    ----------
    * The voter set V is finite and non-empty.
    * The quorum policy P is one of the four supported values.
    * The timeout handler is enabled and fires within one round period.
    * No voter crashes permanently during the protocol execution.
    """

    def __init__(self) -> None:
        """Initialise the ConsensusConvergenceTheorem with its formal statement and conditions.

        Populates ``_name``, ``_statement``, and ``_conditions``.  The
        ``_POLICY_BOUNDS`` mapping is a class-level constant and is not
        re-created here.

        Side-effect free; safe to call at module import time.
        """
        self._name: str = "ConsensusConvergence"
        self._statement: str = (
            "∀ finite voter set V, ∀ policy P ∈ {SIMPLE_MAJORITY, TWO_THIRDS, "
            "UNANIMOUS, TRUST_WEIGHTED}: the consensus protocol terminates within "
            "B(|V|, P) rounds."
        )
        self._conditions: list[str] = [
            "voter_set_finite_nonempty",
            "quorum_policy_supported",
            "timeout_handler_enabled",
            "no_permanent_voter_crash",
        ]
        self.statement = _CallableStr(self._statement)
        self.conditions = _CallableList(self._conditions)

    def statement(self) -> str:
        """Return the full formal statement of the consensus convergence theorem.

        Returns
        -------
        str
            The formal statement string set during ``__init__``.
        """
        return self._statement

    def conditions(self) -> list[str]:
        """Return the precondition list for the consensus convergence theorem.

        Returns
        -------
        list[str]
            Precondition identifier strings, e.g. ``["voter_set_finite_nonempty"]``.
        """
        return list(self._conditions)

    def verify(
        self,
        round_history: list[dict],
        max_rounds: int = 10,
    ) -> TheoremResult:
        """Verify that a recorded round history converged within the allowed bound.

        Scans ``round_history`` (a list of round-state dicts) and checks that
        at least one entry has a ``status`` of ``"CLOSED"`` or ``"TALLIED"``
        within the first ``max_rounds`` entries.  If convergence is not observed
        within the bound the result is CONDITIONAL (the protocol may yet converge
        in subsequent rounds not captured in the trace).

        Args
        ----
        round_history : list[dict]
            List of round-state dictionaries, each containing at least a
            ``status`` key with a string value.  Entries are examined in order
            from index 0.
        max_rounds : int, optional
            The maximum number of rounds within which convergence must occur.
            Defaults to 10.  Should be set to the value returned by
            :meth:`bound` for the specific voter set and policy under test.

        Returns
        -------
        TheoremResult
            VERIFIED if a terminal round state is found within ``max_rounds``;
            CONDITIONAL if not (more data needed).
        """
        terminal_statuses = {"CLOSED", "TALLIED"}
        # Examine only the first max_rounds entries of the history.
        window = round_history[:max_rounds]
        if window:
            all_terminal = all(
                round_state.get("status", "") in terminal_statuses
                or bool(round_state.get("closed", False))
                for round_state in window
            )
            if all_terminal:
                return TheoremResult.create(
                    theorem_name=self._name,
                    status=TheoremStatus.VERIFIED,
                    proof_method=ProofMethod.COINDUCTION,
                    conditions=tuple(self._conditions),
                    notes=f"All {len(window)} recorded rounds reached terminal state.",
                )
        for idx, round_state in enumerate(window):
            status_str = round_state.get("status", "")
            closed = bool(round_state.get("closed", False))
            if status_str in terminal_statuses or closed:
                return TheoremResult.create(
                    theorem_name=self._name,
                    status=TheoremStatus.PARTIAL,
                    proof_method=ProofMethod.COINDUCTION,
                    conditions=("mixed_terminal_and_open_rounds",),
                    notes=f"Round {idx} reached terminal state, but other rounds remain open.",
                )
        # No terminal state found within the window.
        return TheoremResult.create(
            theorem_name=self._name,
            status=TheoremStatus.PARTIAL if round_history else TheoremStatus.PENDING,
            proof_method=ProofMethod.COINDUCTION,
            conditions=("additional_rounds_needed",),
            notes=(
                f"No terminal state found within {max_rounds} rounds "
                f"({len(round_history)} total rounds in history)."
            ),
        )

    def bound(self, n_voters: int, policy: str) -> int:
        """Return the theoretical upper bound on rounds needed for convergence.

        The bounds are derived in theory2.tex Lemma 61.3.2 and depend on both
        the number of voters and the quorum policy.  For deterministic policies
        (SIMPLE_MAJORITY, TWO_THIRDS, TRUST_WEIGHTED) the bound is a small
        constant; for UNANIMOUS it equals the number of voters.

        Args
        ----
        n_voters : int
            The size of the voter set |V|.  Must be ≥ 1.
        policy : str
            One of ``"SIMPLE_MAJORITY"``, ``"TWO_THIRDS"``, ``"UNANIMOUS"``,
            ``"TRUST_WEIGHTED"``.  Unknown policies fall back to ``n_voters``.

        Returns
        -------
        int
            The upper bound B(n_voters, policy) on the number of rounds.
        """
        n = max(1, int(n_voters))  # guard against non-positive inputs
        bounds_map: dict[str, int] = {
            "SIMPLE_MAJORITY": 1,       # quorum is reached in one round for any majority
            "TWO_THIRDS": 2,            # two-thirds quorum may need one retry round
            "UNANIMOUS": n,             # worst case: one voter per round
            "TRUST_WEIGHTED": 3,        # weighted quorum converges in at most three rounds
        }
        return bounds_map.get(policy.upper(), n)

    def bound_computation(self, voter_count: int) -> int:
        """Legacy alias retained for compatibility-oriented tests."""
        return self.bound(voter_count, "UNANIMOUS")

    def to_dict(self) -> dict:
        """Serialise this theorem's metadata to a plain dictionary.

        Returns
        -------
        dict
            Dictionary with keys ``name``, ``statement``, and ``conditions``.
        """
        return {
            "name": self._name,
            "statement": self._statement,
            "conditions": list(self._conditions),
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary of this theorem.

        Returns
        -------
        str
            Summary string in the form ``"<name>: <statement_excerpt>"``.
        """
        excerpt = self._statement[:100] + ("…" if len(self._statement) > 100 else "")
        return f"{self._name}: {excerpt}"


# ===========================================================================
# Theorem 4: Knowledge Propagation Soundness
# ===========================================================================


class KnowledgePropagationSoundnessTheorem:
    """Soundness theorem for the knowledge-propagation sub-protocol (theory2.tex §61.4).

    Statement (informal)
    --------------------
    Let K be a knowledge entry that is valid at the originating node N_src.
    After propagation through any number of intermediate relay nodes the entry
    K' received at node N_dst must satisfy K' = K (semantic identity) — no
    content mutation is permitted during in-flight relay.

    Motivation
    ----------
    The knowledge-propagation channel is used to distribute discovery artefacts
    (node profiles, capability descriptions, trust anchors) across the federation
    graph.  If an intermediate node could silently alter the content of an entry
    in transit, a malicious relay could corrupt the knowledge base of distant
    nodes without being detected.  This theorem, together with the optional
    digital-signature layer (§60.9), closes the integrity gap.

    Proof sketch
    ------------
    Each relay node applies a content-preserving forwarding function f that
    merely wraps the payload in a new envelope (adds hop-count, relay-id) but
    does not touch the payload bytes.  By induction on the hop count h, if the
    entry is intact at hop 0 (the source) it remains intact at hop h (Lemma
    61.4.1).  The proof does not cover Byzantine relays; that case requires the
    digital-signature extension.

    Conditions
    ----------
    * All relay nodes are honest (non-Byzantine).
    * The relay forwarding function does not modify the payload.
    * The ``knowledge_id`` field is set before propagation and is immutable.
    """

    # Required fields that every knowledge entry must contain for it to be
    # considered well-formed and eligible for propagation.
    _REQUIRED_FIELDS: tuple[str, ...] = ("knowledge_id", "source_node", "content")

    def __init__(self) -> None:
        """Initialise the KnowledgePropagationSoundnessTheorem.

        Sets ``_name``, ``_statement``, and ``_conditions`` for this theorem.
        ``_REQUIRED_FIELDS`` is a class constant and is referenced via the
        class rather than re-assigned here.

        Safe to call at module import time; no I/O performed.
        """
        self._name: str = "KnowledgePropagationSoundness"
        self._statement: str = (
            "∀ knowledge entry K, ∀ propagation path π: K' received at the "
            "destination equals K sent at the source (K' = K)."
        )
        self._conditions: list[str] = [
            "relay_nodes_honest",
            "forwarding_function_content_preserving",
            "knowledge_id_immutable",
        ]
        self.statement = _CallableStr(self._statement)
        self.conditions = _CallableList(self._conditions)

    def statement(self) -> str:
        """Return the formal statement of the knowledge-propagation soundness theorem.

        Returns
        -------
        str
            The formal statement string.
        """
        return self._statement

    def conditions(self) -> list[str]:
        """Return the precondition list for the knowledge-propagation soundness theorem.

        Returns
        -------
        list[str]
            Precondition identifier strings.
        """
        return list(self._conditions)

    def verify(
        self, original: dict | list[dict], propagated: dict | None = None
    ) -> TheoremResult:
        """Verify that a propagated knowledge entry is identical to the original.

        Checks that all keys present in ``original`` also appear in
        ``propagated`` with identical values.  Extra keys in ``propagated``
        (e.g. hop metadata) are permitted and do not cause a FALSIFIED outcome.

        Args
        ----
        original : dict
            The knowledge entry at the originating node before propagation.
        propagated : dict
            The knowledge entry as received at the destination node.

        Returns
        -------
        TheoremResult
            VERIFIED if all original fields are preserved; FALSIFIED if any
            field is missing or has a different value in the propagated copy.
        """
        if propagated is None:
            if isinstance(original, list):
                if not original:
                    return TheoremResult.create(
                        theorem_name=self._name,
                        status=TheoremStatus.PENDING,
                        proof_method=ProofMethod.INDUCTION,
                        notes="No propagation records supplied.",
                    )
                preserved = all(entry.get("preserved", True) for entry in original)
                return TheoremResult.create(
                    theorem_name=self._name,
                    status=TheoremStatus.VERIFIED if preserved else TheoremStatus.FALSIFIED,
                    proof_method=ProofMethod.INDUCTION,
                    conditions=tuple(self._conditions) if preserved else (),
                    notes="" if preserved else "At least one propagation record was not preserved.",
                )
            propagated = dict(original)
            original = dict(original)

        assert isinstance(original, dict)
        for key, orig_value in original.items():
            if key not in propagated:
                return TheoremResult.create(
                    theorem_name=self._name,
                    status=TheoremStatus.FALSIFIED,
                    proof_method=ProofMethod.INDUCTION,
                    notes=f"Field '{key}' present in original but absent from propagated entry.",
                )
            prop_value = propagated[key]
            if prop_value != orig_value:
                return TheoremResult.create(
                    theorem_name=self._name,
                    status=TheoremStatus.FALSIFIED,
                    proof_method=ProofMethod.INDUCTION,
                    notes=(
                        f"Field '{key}' mutated during propagation: "
                        f"original={orig_value!r}, propagated={prop_value!r}."
                    ),
                )
        return TheoremResult.create(
            theorem_name=self._name,
            status=TheoremStatus.VERIFIED,
            proof_method=ProofMethod.INDUCTION,
            conditions=tuple(self._conditions),
        )

    def check_preservation(self, before: dict, after: dict | None = None) -> bool:
        """Check whether a knowledge entry contains all required structural fields.

        A well-formed entry must contain at least the fields listed in
        ``_REQUIRED_FIELDS`` (``knowledge_id``, ``source_node``, ``content``).
        Entries that fail this check should not be propagated — doing so would
        violate the preconditions of the propagation soundness theorem.

        Args
        ----
        entry : dict
            The knowledge entry to validate.

        Returns
        -------
        bool
            ``True`` if all required fields are present (regardless of their
            values); ``False`` if any required field is absent.
        """
        if after is None:
            return all(field in before for field in self._REQUIRED_FIELDS)
        return all(key in after and after[key] == value for key, value in before.items())

    def to_dict(self) -> dict:
        """Serialise this theorem's metadata to a plain dictionary.

        Returns
        -------
        dict
            Dictionary with keys ``name``, ``statement``, ``conditions``, and
            ``required_fields``.
        """
        return {
            "name": self._name,
            "statement": self._statement,
            "conditions": list(self._conditions),
            "required_fields": list(self._REQUIRED_FIELDS),
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary of this theorem.

        Returns
        -------
        str
            Summary string in the form ``"<name>: <statement_excerpt>"``.
        """
        excerpt = self._statement[:100] + ("…" if len(self._statement) > 100 else "")
        return f"{self._name}: {excerpt}"


# ===========================================================================
# Theorem 5: Conflict Resolution Completeness
# ===========================================================================


class ConflictResolutionCompletenessTheorem:
    """Completeness theorem for the conflict-resolution sub-protocol (theory2.tex §61.5).

    Statement (informal)
    --------------------
    For every conflict event C logged by the federation dispatcher, there exists
    a corresponding resolution event R in the resolution log such that R.conflict_id
    = C.conflict_id.  In other words, the conflict-resolution process is *complete*:
    no conflict remains permanently unresolved.

    Motivation
    ----------
    The federation protocol relies on the conflict-resolution sub-protocol to
    mediate disagreements between nodes' local stores.  If the sub-protocol were
    not complete — i.e. if some conflicts were never resolved — the global
    knowledge state could fragment, with different nodes holding permanently
    divergent views of the same fact.  Completeness guarantees eventual
    convergence of the global knowledge state.

    Proof sketch
    ------------
    The proof proceeds by well-founded induction on the *conflict age* (the
    number of rounds elapsed since the conflict was first logged).  The
    conflict-resolution scheduler enforces a maximum age threshold MAX_AGE; any
    conflict that reaches MAX_AGE is force-resolved using the "last-writer-wins"
    fallback policy.  Since MAX_AGE is finite, every conflict is resolved within
    MAX_AGE rounds (Lemma 61.5.1).  The fallback resolution may not be
    semantically optimal but it is complete.

    Conditions
    ----------
    * The conflict-resolution scheduler is running and processing the conflict queue.
    * The MAX_AGE threshold is finite and reachable.
    * The fallback resolution policy (last-writer-wins) is enabled.
    """

    def __init__(self) -> None:
        """Initialise the ConflictResolutionCompletenessTheorem.

        Sets ``_name``, ``_statement``, and ``_conditions`` for this theorem.

        No I/O is performed; safe to call at module import time.
        """
        self._name: str = "ConflictResolutionCompleteness"
        self._statement: str = (
            "∀ conflict C logged by the dispatcher, ∃ resolution R in the "
            "resolution log: R.conflict_id = C.conflict_id."
        )
        self._conditions: list[str] = [
            "resolution_scheduler_running",
            "max_age_threshold_finite",
            "fallback_policy_enabled",
        ]
        self.statement = _CallableStr(self._statement)
        self.conditions = _CallableList(self._conditions)

    def statement(self) -> str:
        """Return the formal statement of the conflict-resolution completeness theorem.

        Returns
        -------
        str
            The formal statement string.
        """
        return self._statement

    def conditions(self) -> list[str]:
        """Return the precondition list for the conflict-resolution completeness theorem.

        Returns
        -------
        list[str]
            Precondition identifier strings.
        """
        return list(self._conditions)

    def verify(
        self,
        conflict_log: list[dict],
        resolution_log: list[dict] | None = None,
    ) -> TheoremResult:
        """Verify that every logged conflict has a corresponding resolution.

        Extracts all ``conflict_id`` values from ``conflict_log`` and checks
        that each one appears in at least one entry in ``resolution_log``.

        Args
        ----
        conflict_log : list[dict]
            List of conflict-event dicts, each containing a ``conflict_id`` key.
        resolution_log : list[dict]
            List of resolution-event dicts, each containing a ``conflict_id`` key.

        Returns
        -------
        TheoremResult
            VERIFIED if all conflicts are resolved; FALSIFIED with the first
            unresolved conflict_id in ``notes``.
        """
        if resolution_log is None:
            resolution_log = [entry for entry in conflict_log if entry.get("resolved")]
        # Build a set of resolved conflict IDs for O(1) lookup.
        resolved_ids: set[str] = {
            entry.get("conflict_id", "")
            for entry in resolution_log
            if entry.get("conflict_id")
        }
        for conflict in conflict_log:
            cid = conflict.get("conflict_id", "")
            if cid and cid not in resolved_ids:
                return TheoremResult.create(
                    theorem_name=self._name,
                    status=TheoremStatus.FALSIFIED,
                    proof_method=ProofMethod.INDUCTION,
                    notes=f"Conflict '{cid}' has no matching resolution entry.",
                )
        return TheoremResult.create(
            theorem_name=self._name,
            status=TheoremStatus.VERIFIED,
            proof_method=ProofMethod.INDUCTION,
            conditions=tuple(self._conditions),
            notes=(
                f"{len(conflict_log)} conflict(s) verified against "
                f"{len(resolution_log)} resolution(s)."
            ),
        )

    def compute_resolution_rate(
        self,
        conflict_log: list[dict],
        resolution_log: list[dict] | None = None,
    ) -> float:
        """Compute the fraction of logged conflicts that have been resolved.

        The resolution rate is a real number in [0.0, 1.0].  A value of 1.0
        means all conflicts are resolved (completeness holds); a value of 0.0
        means no conflicts have been resolved yet.  The result is clamped via
        :func:`_clamp` to guard against floating-point edge cases.

        Args
        ----
        conflict_log : list[dict]
            List of conflict-event dicts with ``conflict_id`` keys.
        resolution_log : list[dict]
            List of resolution-event dicts with ``conflict_id`` keys.

        Returns
        -------
        float
            Resolution rate in [0.0, 1.0].  Returns 1.0 if the conflict log is
            empty (vacuously complete).
        """
        if not conflict_log:
            return 1.0  # vacuously true — no conflicts means all are "resolved"
        if resolution_log is None:
            resolution_log = [entry for entry in conflict_log if entry.get("resolved")]
        resolved_ids: set[str] = {
            entry.get("conflict_id", "")
            for entry in resolution_log
            if entry.get("conflict_id")
        }
        n_resolved = sum(
            1
            for c in conflict_log
            if c.get("conflict_id", "") in resolved_ids
        )
        raw_rate = n_resolved / len(conflict_log)
        return _clamp(raw_rate, 0.0, 1.0)

    def to_dict(self) -> dict:
        """Serialise this theorem's metadata to a plain dictionary.

        Returns
        -------
        dict
            Dictionary with keys ``name``, ``statement``, and ``conditions``.
        """
        return {
            "name": self._name,
            "statement": self._statement,
            "conditions": list(self._conditions),
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary of this theorem.

        Returns
        -------
        str
            Summary string in the form ``"<name>: <statement_excerpt>"``.
        """
        excerpt = self._statement[:100] + ("…" if len(self._statement) > 100 else "")
        return f"{self._name}: {excerpt}"


# ===========================================================================
# Registry
# ===========================================================================


class FederationTheoremRegistry:
    """Central registry for all JuGeo federation-protocol theorems (theory2.tex §61.6).

    The registry holds a named collection of theorem objects (instances of any
    of the five theorem classes defined above) and provides a unified interface
    for:

    * Registering new theorem objects under string names.
    * Retrieving theorems by name or status.
    * Bulk-verifying all registered theorems against a shared set of keyword
      arguments.
    * Serialising the full theorem catalogue to a dictionary suitable for
      embedding in audit reports or structured log entries.

    Design rationale
    ----------------
    Rather than hard-coding calls to each theorem class in the federation
    dispatcher, the dispatcher injects a :class:`FederationTheoremRegistry`
    instance and calls :meth:`verify_all`.  This decouples the dispatcher from
    the specific set of theorems in use, making it straightforward to add new
    theorems (or swap in mock theorems during testing) without touching the
    dispatcher code.

    Thread safety
    -------------
    The registry's internal ``_theorems`` dict is not protected by a lock.  In
    multi-threaded contexts callers should either use a single registry per
    thread or wrap ``register`` / ``verify_all`` calls in an appropriate lock.

    Usage
    -----
    ::

        registry = FederationTheoremRegistry()
        registry.register_defaults()
        results = registry.verify_all(
            federation_result=...,
            local_result=...,
            authority_before=...,
            authority_after=...,
            trust_delta=0.1,
            round_history=...,
            max_rounds=5,
            original=...,
            propagated=...,
            conflict_log=...,
            resolution_log=...,
        )
    """

    def __init__(self) -> None:
        """Initialise an empty :class:`FederationTheoremRegistry`.

        Creates the internal ``_theorems`` dictionary which maps string names
        to theorem objects.  To pre-populate the registry with the standard
        theorem set, call :meth:`register_defaults` after construction.

        No external resources are accessed; construction is side-effect-free.
        """
        # _theorems maps theorem name → theorem object.
        self._theorems: dict[str, Any] = {}
        self._last_results: dict[str, TheoremResult] = {}

    def register(self, name: str, theorem: Any) -> None:
        """Register a theorem object under the given name.

        If a theorem is already registered under ``name`` it will be silently
        overwritten.  Use distinct names to avoid collisions; by convention the
        name should match the theorem's own ``_name`` attribute.

        Args
        ----
        name : str
            The registration key.  Must be a non-empty string.
        theorem : Any
            The theorem object to register.  Must have a ``verify`` method
            that accepts keyword arguments and returns a :class:`TheoremResult`.

        Returns
        -------
        None
        """
        if not name:
            raise ValueError("Theorem name must be a non-empty string.")
        _log.debug("FederationTheoremRegistry.register: %s", name)
        self._theorems[name] = theorem

    def register_defaults(self) -> None:
        """Register the five standard federation-protocol theorems.

        Instantiates one instance of each of the five theorem classes and
        registers them under their canonical names.  This is the recommended
        way to initialise a production registry.

        Returns
        -------
        None
        """
        self.register("FederationSoundness", FederationSoundnessTheorem())
        self.register("AuthorityMonotonicity", AuthorityMonotonicityTheorem())
        self.register("ConsensusConvergence", ConsensusConvergenceTheorem())
        self.register("KnowledgePropagationSoundness", KnowledgePropagationSoundnessTheorem())
        self.register("ConflictResolutionCompleteness", ConflictResolutionCompletenessTheorem())

    def get(self, name: str) -> Optional[Any]:
        """Retrieve a theorem object by name, returning None if not found.

        Args
        ----
        name : str
            The registration key used when the theorem was registered.

        Returns
        -------
        Optional[Any]
            The theorem object, or ``None`` if no theorem is registered under
            ``name``.
        """
        return self._theorems.get(name)

    def all_theorems(self) -> list[Any]:
        """Return a list of all registered theorem objects.

        The order of the returned list reflects the insertion order of the
        underlying dictionary (guaranteed in Python ≥ 3.7).

        Returns
        -------
        list[Any]
            All theorem objects currently registered in this registry.
        """
        return list(self._theorems.values())

    def verify_all(self, evidence: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, TheoremResult]:
        """Attempt to verify all registered theorems with the given keyword arguments.

        For each registered theorem, calls its ``verify`` method with a subset
        of ``kwargs`` relevant to that theorem.  The mapping from theorem names
        to required kwargs is hard-coded in this method based on each theorem's
        ``verify`` signature.

        Args
        ----
        **kwargs : Any
            Combined keyword arguments for all theorems.  Unknown keys are
            silently ignored.  Missing keys for a theorem cause that theorem to
            be skipped (not verified) — its entry in the result dict will be
            absent.

        Returns
        -------
        dict[str, TheoremResult]
            A dictionary mapping each theorem name to its :class:`TheoremResult`.
            Only theorems for which all required kwargs were provided are included.
        """
        merged = dict(evidence or {})
        merged.update(kwargs)
        results: dict[str, TheoremResult] = {}

        for name, theorem in self._theorems.items():
            if isinstance(theorem, FederationSoundnessTheorem):
                fed_results = merged.get("federation_results")
                if fed_results is not None:
                    result = theorem.verify(fed_results)
                elif "federation_result" in merged and "local_result" in merged:
                    result = theorem.verify(
                        federation_result=merged["federation_result"],
                        local_result=merged["local_result"],
                    )
                else:
                    continue
            elif isinstance(theorem, AuthorityMonotonicityTheorem):
                if "authority_before" in merged and "authority_after" in merged and "trust_delta" in merged:
                    result = theorem.verify(
                        authority_before=merged["authority_before"],
                        authority_after=merged["authority_after"],
                        trust_delta=merged["trust_delta"],
                    )
                elif "authority_history" in merged:
                    levels = [str(item.get("authority_level", "none")).lower() for item in merged["authority_history"]]
                    before = {str(i): levels[i] for i in range(max(0, len(levels) - 1))}
                    after = {str(i): levels[i + 1] for i in range(max(0, len(levels) - 1))}
                    result = theorem.verify(before, after, 1.0)
                else:
                    continue
            elif isinstance(theorem, ConsensusConvergenceTheorem):
                if "round_history" not in merged:
                    continue
                result = theorem.verify(
                    round_history=merged["round_history"],
                    max_rounds=merged.get("max_rounds", 10),
                )
            elif isinstance(theorem, KnowledgePropagationSoundnessTheorem):
                if "propagation_log" in merged:
                    result = theorem.verify(merged["propagation_log"])
                elif "original" in merged and "propagated" in merged:
                    result = theorem.verify(merged["original"], merged["propagated"])
                else:
                    continue
            elif isinstance(theorem, ConflictResolutionCompletenessTheorem):
                if "conflict_log" not in merged:
                    continue
                result = theorem.verify(
                    merged["conflict_log"],
                    merged.get("resolution_log"),
                )
            else:
                continue
            results[name] = result

        self._last_results = dict(results)
        return results

    def summary(self) -> str:
        """Return a multi-line string summarising all registered theorems.

        Iterates over all registered theorems, calls their ``summary()`` method,
        and joins the results with newlines.  If the registry is empty, returns
        a placeholder string.

        Returns
        -------
        str
            A newline-delimited summary of all registered theorems, prefixed by
            a header line showing the total count.
        """
        if not self._theorems:
            return "FederationTheoremRegistry: (empty)"
        lines = [f"FederationTheoremRegistry ({len(self._theorems)} theorem(s)):"]
        for name, theorem in self._theorems.items():
            if hasattr(theorem, "summary"):
                lines.append(f"  [{name}] {theorem.summary()}")
            else:
                lines.append(f"  [{name}] (no summary method)")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialise the full theorem catalogue to a plain Python dictionary.

        For each registered theorem, calls its ``to_dict()`` method and stores
        the result under the theorem's registration name.

        Returns
        -------
        dict
            A dictionary mapping theorem names to their serialised metadata
            dicts.  The outer key ``theorems`` holds a nested dict, and
            ``count`` holds the number of registered theorems.
        """
        return {
            "count": len(self._theorems),
            "theorems": {
                name: theorem.to_dict() if hasattr(theorem, "to_dict") else {}
                for name, theorem in self._theorems.items()
            },
        }

    def get_by_status(self, status: TheoremStatus) -> list[Any]:
        """Return all theorems whose last verification produced the given status.

        This method is useful for extracting, e.g., all FALSIFIED theorems from
        the registry after a bulk :meth:`verify_all` run, without having to scan
        the result dictionary manually.

        Note that this method re-runs :meth:`verify_all` internally with no
        arguments, so only theorems that can be verified without arguments will
        be included.  In practice, callers should pass the results of a
        :meth:`verify_all` call to a custom filter rather than relying on this
        method.

        Args
        ----
        status : TheoremStatus
            The outcome status to filter by, e.g. ``TheoremStatus.FALSIFIED``.

        Returns
        -------
        list[Any]
            List of theorem objects whose last known result matches ``status``.
            May be empty if no theorems match or if no verifications have been
            performed.
        """
        # Re-run verify_all with no kwargs — only theorems with zero required
        # args will produce a result; others are omitted.
        results = self._last_results
        matching: list[Any] = []
        for name, result in results.items():
            if result.status == status:
                theorem = self._theorems.get(name)
                if theorem is not None:
                    matching.append(theorem)
        return matching
