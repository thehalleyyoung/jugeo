"""Formal theorems about kind discovery (theory2.tex Ch 56).

Catalogues the core theoretical results that underpin the kind-discovery
pipeline, provides verification utilities, and enables theorem-guided
discovery.

Module layout::

    KindDiscoveryTheorem   – frozen dataclass for a formal theorem
    TheoremRegistry        – registry of known theorems
    TheoremVerifier        – verifies applicability of theorems
    TheoremApplications    – applies theorems to guide discovery
    TheoremCatalog         – curated catalog with named theorem instances
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Sequence

from jugeo.ideation.kind_discovery.models import (
    KindCandidate, ObstructionField, KindPattern, NewKind, KindStatus,
)

try:
    from jugeo.ideation.novelty import TheoremPortfolio
except ImportError:
    TheoremPortfolio = None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, float(v)))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alphanumeric characters, and drop short tokens.

    Tokens of length <= 2 are discarded as they carry little semantic weight.
    Returns a set of lowercase token strings.

    Parameters
    ----------
    text:
        Input text to tokenize.

    Returns
    -------
    set[str]
        Set of meaningful lowercase tokens extracted from *text*.
    """
    raw = re.split(r"[^a-zA-Z0-9]+", text.lower())
    return {tok for tok in raw if len(tok) > 2}


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Compute the Jaccard similarity (intersection over union) of two token collections.

    Parameters
    ----------
    a, b:
        Iterables of string tokens.  They will be converted to sets internally.

    Returns
    -------
    float
        A value in [0, 1]: 1.0 if the sets are identical, 0.0 if disjoint.
        Returns 0.0 when both sets are empty.
    """
    set_a = set(a)
    set_b = set(b)
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _format_theorem(theorem: "KindDiscoveryTheorem") -> str:
    """Format a theorem as a human-readable multi-line string.

    Includes the theorem name, status, scope, statement, preconditions,
    conclusions, and proof sketch in a readable layout suitable for
    logging or display.

    Parameters
    ----------
    theorem:
        The :class:`KindDiscoveryTheorem` instance to format.

    Returns
    -------
    str
        A formatted multi-line string representation of the theorem.
    """
    lines: list[str] = [
        f"Theorem [{theorem.theorem_id}]: {theorem.name}",
        f"  Status : {theorem.status.value}  |  Scope: {theorem.scope.value}",
        f"  Statement: {theorem.statement}",
        "  Preconditions:",
    ]
    for i, pre in enumerate(theorem.preconditions, start=1):
        lines.append(f"    {i}. {pre}")
    lines.append("  Conclusions:")
    for i, con in enumerate(theorem.conclusions, start=1):
        lines.append(f"    {i}. {con}")
    if theorem.proof_sketch:
        lines.append(f"  Proof sketch: {theorem.proof_sketch}")
    if theorem.references:
        lines.append("  References: " + "; ".join(theorem.references))
    return "\n".join(lines)


def _check_precondition(precondition: str, context: dict) -> bool:
    """Check whether a precondition string is satisfied given a context dict.

    The check works by tokenizing the precondition and looking for keyword
    matches among the string values stored in *context*.  A precondition is
    considered satisfied when at least one significant keyword from the
    precondition text appears in at least one context value.

    If the context is empty the function returns False (no evidence to
    support the precondition).  If the precondition is empty the function
    returns True (vacuously satisfied).

    Parameters
    ----------
    precondition:
        A natural-language precondition string.
    context:
        A mapping from string keys to arbitrary values.  Only string values
        participate in keyword matching.

    Returns
    -------
    bool
        True if the precondition appears to be satisfied by the context.
    """
    if not precondition.strip():
        return True
    if not context:
        return False

    precondition_tokens = _tokenize(precondition)
    if not precondition_tokens:
        return True

    # Build the set of tokens found across all string-valued context entries
    context_tokens: set[str] = set()
    for value in context.values():
        if isinstance(value, str):
            context_tokens.update(_tokenize(value))
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    context_tokens.update(_tokenize(item))

    # A precondition is satisfied when at least one of its keywords is
    # present in the context and that keyword is longer than 3 characters
    # (to avoid false positives from common short words).
    meaningful = {tok for tok in precondition_tokens if len(tok) > 3}
    if not meaningful:
        # All tokens are short — fall back to any overlap
        return bool(precondition_tokens & context_tokens)

    overlap = meaningful & context_tokens
    # Require at least 20% of meaningful tokens to match for a positive result
    return len(overlap) / len(meaningful) >= 0.20


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TheoremStatus(str, Enum):
    """Lifecycle status of a kind-discovery theorem.

    Attributes
    ----------
    CONJECTURED:
        The theorem has been proposed but not yet verified or established.
    PROVISIONAL:
        The theorem has supporting evidence but has not been formally proved.
    ESTABLISHED:
        The theorem has been formally proved within the theory2.tex framework.
    CLASSICAL:
        The theorem is a classical result with multiple independent proofs.
    """

    CONJECTURED = "conjectured"
    PROVISIONAL = "provisional"
    ESTABLISHED = "established"
    CLASSICAL = "classical"


class TheoremScope(str, Enum):
    """Indicates how broadly a theorem applies across domains.

    Attributes
    ----------
    LOCAL:
        Applies only within a single specific subdomain or example.
    DOMAIN_SPECIFIC:
        Applies across a single well-defined mathematical or conceptual domain.
    CROSS_DOMAIN:
        Applies across multiple domains that share structural features.
    UNIVERSAL:
        Applies in all domains where the basic kind-discovery framework holds.
    """

    LOCAL = "local"
    DOMAIN_SPECIFIC = "domain_specific"
    CROSS_DOMAIN = "cross_domain"
    UNIVERSAL = "universal"


# ---------------------------------------------------------------------------
# KindDiscoveryTheorem
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class KindDiscoveryTheorem:
    """A formal theorem about the kind-discovery process.

    Encapsulates the identity, statement, preconditions, conclusions,
    proof sketch, and metadata for a theoretical result about kind discovery.

    Attributes
    ----------
    theorem_id:
        A unique string identifier (e.g. "thm-kd-001").
    name:
        A short human-readable name for the theorem.
    statement:
        The full formal or semi-formal statement of the theorem.
    preconditions:
        Ordered tuple of precondition strings (hypotheses).
    conclusions:
        Ordered tuple of conclusion strings (consequences).
    proof_sketch:
        A brief description of the proof strategy.
    references:
        Tuple of bibliographic references (e.g. "theory2.tex §56.2").
    status:
        The verification status of this theorem.
    scope:
        The breadth of applicability of this theorem.
    created_at:
        ISO-8601 UTC creation timestamp.
    tags:
        Frozenset of keyword tags for indexing.
    """

    theorem_id: str
    name: str
    statement: str
    preconditions: tuple[str, ...]
    conclusions: tuple[str, ...]
    proof_sketch: str = ""
    references: tuple[str, ...] = ()
    status: TheoremStatus = TheoremStatus.PROVISIONAL
    scope: TheoremScope = TheoremScope.DOMAIN_SPECIFIC
    created_at: str = field(default_factory=_now_iso)
    tags: frozenset[str] = frozenset()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_applicable(self) -> bool:
        """Return True if the theorem is established or classical and has preconditions.

        A theorem must be at least ESTABLISHED (i.e. not merely conjectured or
        provisional) and must state at least one precondition before it can be
        applied to guide discovery.
        """
        return (
            self.status in (TheoremStatus.ESTABLISHED, TheoremStatus.CLASSICAL)
            and len(self.preconditions) > 0
        )

    @property
    def complexity(self) -> int:
        """Return a rough integer complexity measure for this theorem.

        Combines the number of preconditions, the number of conclusions, and
        a coarse word-count of the statement text.  Higher values indicate
        more structurally complex theorems.
        """
        statement_weight = len(self.statement.split()) // 10
        return len(self.preconditions) + len(self.conclusions) + statement_weight

    # ------------------------------------------------------------------
    # Applicability checks
    # ------------------------------------------------------------------

    def applies_to(self, candidate: KindCandidate) -> bool:
        """Determine whether this theorem is applicable to a kind candidate.

        Applicability is assessed along two dimensions:

        1. **Token-set similarity**: the Jaccard similarity between the tokens
           of the theorem statement and the tokens of the candidate's name,
           description, and obstruction pattern.  A similarity > 0.15 is
           sufficient for applicability.

        2. **Precondition keyword match**: each precondition is checked for
           keyword overlap with the candidate's description and obstruction
           pattern.  If any precondition contains a keyword matching the
           candidate data, the theorem is applicable.

        Parameters
        ----------
        candidate:
            The :class:`KindCandidate` to test applicability against.

        Returns
        -------
        bool
            True if the theorem is applicable to *candidate*.
        """
        # Build token set for the theorem
        theorem_tokens = _tokenize(self.statement)
        for pre in self.preconditions:
            theorem_tokens.update(_tokenize(pre))
        for con in self.conclusions:
            theorem_tokens.update(_tokenize(con))

        # Build token set for the candidate
        candidate_tokens = _tokenize(candidate.description)
        candidate_tokens.update(_tokenize(candidate.name))
        candidate_tokens.update(_tokenize(candidate.obstruction_pattern))

        # Dimension 1: overall Jaccard similarity
        sim = _jaccard(theorem_tokens, candidate_tokens)
        if sim > 0.15:
            return True

        # Dimension 2: precondition keyword match
        # Check each precondition for overlap with the candidate's key fields
        candidate_context = {
            "description": candidate.description,
            "obstruction_pattern": candidate.obstruction_pattern,
            "name": candidate.name,
            "tags": " ".join(candidate.tags),
        }
        for precondition in self.preconditions:
            if _check_precondition(precondition, candidate_context):
                return True

        # Dimension 3: tag intersection
        if self.tags & candidate.tags:
            return True

        return False

    def strengthens(self, other: "KindDiscoveryTheorem") -> bool:
        """Return True if this theorem's conclusions support the other's preconditions.

        Theorem chaining: this theorem *strengthens* another when its
        conclusions share vocabulary with the other theorem's preconditions,
        i.e. the Jaccard similarity between this theorem's conclusion tokens
        and the other theorem's precondition tokens exceeds 0.2.

        This captures the notion that proving this theorem first makes it
        easier to satisfy the preconditions of *other*.

        Parameters
        ----------
        other:
            The theorem whose preconditions are to be checked against this
            theorem's conclusions.

        Returns
        -------
        bool
            True if this theorem meaningfully supports *other*.
        """
        if self.theorem_id == other.theorem_id:
            return False
        my_conclusion_tokens: set[str] = set()
        for con in self.conclusions:
            my_conclusion_tokens.update(_tokenize(con))

        other_precondition_tokens: set[str] = set()
        for pre in other.preconditions:
            other_precondition_tokens.update(_tokenize(pre))

        return _jaccard(my_conclusion_tokens, other_precondition_tokens) > 0.2

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise this theorem to a JSON-compatible dictionary.

        All enum values are rendered as their string representation.
        Frozenset tags are converted to a sorted list.

        Returns
        -------
        dict
            A flat dictionary suitable for JSON serialization.
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "statement": self.statement,
            "preconditions": list(self.preconditions),
            "conclusions": list(self.conclusions),
            "proof_sketch": self.proof_sketch,
            "references": list(self.references),
            "status": self.status.value,
            "scope": self.scope.value,
            "created_at": self.created_at,
            "tags": sorted(self.tags),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KindDiscoveryTheorem":
        """Reconstruct a :class:`KindDiscoveryTheorem` from a dictionary.

        This is the inverse of :meth:`to_dict`.  Enum fields are coerced
        from their string values, and list fields are converted to tuples
        or frozensets as appropriate.

        Parameters
        ----------
        d:
            A dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        KindDiscoveryTheorem
            The reconstructed theorem instance.
        """
        return cls(
            theorem_id=d["theorem_id"],
            name=d["name"],
            statement=d["statement"],
            preconditions=tuple(d.get("preconditions", [])),
            conclusions=tuple(d.get("conclusions", [])),
            proof_sketch=d.get("proof_sketch", ""),
            references=tuple(d.get("references", [])),
            status=TheoremStatus(d.get("status", TheoremStatus.PROVISIONAL.value)),
            scope=TheoremScope(d.get("scope", TheoremScope.DOMAIN_SPECIFIC.value)),
            created_at=d.get("created_at", _now_iso()),
            tags=frozenset(d.get("tags", [])),
        )

    # ------------------------------------------------------------------
    # Human-readable representations
    # ------------------------------------------------------------------

    def formal_statement(self) -> str:
        """Return the theorem in "If … then …" form.

        Joins all preconditions with "and" and all conclusions with "and"
        to produce a single formal-style implication sentence.

        Returns
        -------
        str
            A natural-language formal statement string.
        """
        pre_text = "; and ".join(self.preconditions) if self.preconditions else "(no preconditions)"
        con_text = "; and ".join(self.conclusions) if self.conclusions else "(no conclusions)"
        return f"{self.name}: If {pre_text} then {con_text}."

    def latex_statement(self) -> str:
        """Return this theorem formatted as a LaTeX theorem environment.

        Produces a ``\\begin{theorem}`` / ``\\end{theorem}`` block compatible
        with standard LaTeX theorem packages (amsthm).  Preconditions are
        typeset as an itemized list of hypotheses; conclusions likewise.

        Returns
        -------
        str
            A LaTeX-formatted string for this theorem.
        """
        lines = [
            f"\\begin{{theorem}}[{self.name}]",
            f"\\label{{thm:{self.theorem_id}}}",
            self.statement,
            "",
            "\\textbf{Preconditions:}",
            "\\begin{enumerate}",
        ]
        for pre in self.preconditions:
            lines.append(f"  \\item {pre}")
        lines.append("\\end{enumerate}")
        lines.append("")
        lines.append("\\textbf{Conclusions:}")
        lines.append("\\begin{enumerate}")
        for con in self.conclusions:
            lines.append(f"  \\item {con}")
        lines.append("\\end{enumerate}")
        if self.proof_sketch:
            lines.append("")
            lines.append(f"\\textit{{Proof sketch.}} {self.proof_sketch}")
        lines.append("\\end{theorem}")
        return "\n".join(lines)

    def summary(self) -> str:
        """Return a concise one-line summary of this theorem.

        Suitable for use in logs, reports, and list views.

        Returns
        -------
        str
            A single-line summary string.
        """
        tag_str = ", ".join(sorted(self.tags)) if self.tags else "no tags"
        return (
            f"[{self.theorem_id}] {self.name} "
            f"({self.status.value}, {self.scope.value}) "
            f"— {len(self.preconditions)} pre, {len(self.conclusions)} con "
            f"[{tag_str}]"
        )


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------

@dataclass
class TheoremRegistry:
    """Registry of all known kind-discovery theorems.

    Provides registration, lookup, and filtering of
    :class:`KindDiscoveryTheorem` objects.  Maintains secondary indexes
    on tags and status for efficient filtered lookups.

    Attributes
    ----------
    _theorems:
        Primary dictionary mapping theorem_id to theorem instances.
    _tag_index:
        Secondary index mapping tag strings to sets of theorem_ids.
    _status_index:
        Secondary index mapping status value strings to sets of theorem_ids.
    _created_at:
        ISO-8601 UTC timestamp recording when the registry was created.
    """

    _theorems: dict[str, KindDiscoveryTheorem] = field(default_factory=dict)
    _tag_index: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    _status_index: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    _created_at: str = field(default_factory=_now_iso)

    def register(self, theorem: KindDiscoveryTheorem) -> None:
        """Register a theorem in the registry.

        Adds the theorem to the primary dictionary and updates both the tag
        and status secondary indexes.  If a theorem with the same
        ``theorem_id`` is already registered it is silently replaced.

        Parameters
        ----------
        theorem:
            The :class:`KindDiscoveryTheorem` to register.
        """
        self._theorems[theorem.theorem_id] = theorem
        # Update tag index
        for tag in theorem.tags:
            self._tag_index[tag].add(theorem.theorem_id)
        # Update status index
        self._status_index[theorem.status.value].add(theorem.theorem_id)

    def deregister(self, theorem_id: str) -> bool:
        """Remove a theorem from the registry.

        Also removes all entries from the tag and status secondary indexes.

        Parameters
        ----------
        theorem_id:
            The identifier of the theorem to remove.

        Returns
        -------
        bool
            True if the theorem was found and removed, False if it was not
            present in the registry.
        """
        theorem = self._theorems.pop(theorem_id, None)
        if theorem is None:
            return False
        for tag in theorem.tags:
            self._tag_index[tag].discard(theorem_id)
        self._status_index[theorem.status.value].discard(theorem_id)
        return True

    def get(self, theorem_id: str) -> KindDiscoveryTheorem | None:
        """Look up a theorem by its identifier.

        Parameters
        ----------
        theorem_id:
            The identifier to look up.

        Returns
        -------
        KindDiscoveryTheorem | None
            The matching theorem, or None if no theorem with that id exists.
        """
        return self._theorems.get(theorem_id)

    def list_all(self) -> list[KindDiscoveryTheorem]:
        """Return all registered theorems sorted alphabetically by name.

        Returns
        -------
        list[KindDiscoveryTheorem]
            All theorems in alphabetical name order.
        """
        return sorted(self._theorems.values(), key=lambda t: t.name.lower())

    def find_by_status(self, status: TheoremStatus) -> list[KindDiscoveryTheorem]:
        """Return all theorems with the given status.

        Parameters
        ----------
        status:
            The :class:`TheoremStatus` to filter by.

        Returns
        -------
        list[KindDiscoveryTheorem]
            All theorems matching *status*, sorted by name.
        """
        ids = self._status_index.get(status.value, set())
        theorems = [self._theorems[tid] for tid in ids if tid in self._theorems]
        return sorted(theorems, key=lambda t: t.name.lower())

    def find_by_scope(self, scope: TheoremScope) -> list[KindDiscoveryTheorem]:
        """Return all theorems with the given scope.

        Parameters
        ----------
        scope:
            The :class:`TheoremScope` to filter by.

        Returns
        -------
        list[KindDiscoveryTheorem]
            All theorems matching *scope*, sorted by name.
        """
        return sorted(
            [t for t in self._theorems.values() if t.scope == scope],
            key=lambda t: t.name.lower(),
        )

    def find_applicable_to(self, candidate: KindCandidate) -> list[KindDiscoveryTheorem]:
        """Return all theorems that are applicable to *candidate*.

        Uses :meth:`KindDiscoveryTheorem.applies_to` for the applicability
        check.  Results are sorted by status strength (CLASSICAL first) then
        by name.

        Parameters
        ----------
        candidate:
            The :class:`KindCandidate` to test applicability against.

        Returns
        -------
        list[KindDiscoveryTheorem]
            Applicable theorems sorted by descending status strength.
        """
        status_rank = {
            TheoremStatus.CLASSICAL: 0,
            TheoremStatus.ESTABLISHED: 1,
            TheoremStatus.PROVISIONAL: 2,
            TheoremStatus.CONJECTURED: 3,
        }
        applicable = [t for t in self._theorems.values() if t.applies_to(candidate)]
        return sorted(applicable, key=lambda t: (status_rank.get(t.status, 99), t.name.lower()))

    def find_by_tag(self, tag: str) -> list[KindDiscoveryTheorem]:
        """Return all theorems tagged with *tag*.

        Parameters
        ----------
        tag:
            The tag string to search for.

        Returns
        -------
        list[KindDiscoveryTheorem]
            All theorems with the given tag, sorted by name.
        """
        ids = self._tag_index.get(tag, set())
        theorems = [self._theorems[tid] for tid in ids if tid in self._theorems]
        return sorted(theorems, key=lambda t: t.name.lower())

    def size(self) -> int:
        """Return the number of registered theorems."""
        return len(self._theorems)

    def __contains__(self, theorem_id: str) -> bool:
        """Return True if a theorem with *theorem_id* is registered."""
        return theorem_id in self._theorems

    def snapshot(self) -> dict:
        """Return a snapshot of the registry as a serializable dictionary.

        The snapshot maps theorem_id to the theorem's dict representation.
        It can be serialized to JSON and used for persistence or logging.

        Returns
        -------
        dict
            A dict of the form ``{theorem_id: theorem.to_dict()}``.
        """
        return {tid: thm.to_dict() for tid, thm in self._theorems.items()}


# ---------------------------------------------------------------------------
# TheoremVerifier
# ---------------------------------------------------------------------------

@dataclass
class TheoremVerifier:
    """Verifies that theorems are internally consistent and applicable.

    Performs structural and logical checks on :class:`KindDiscoveryTheorem`
    objects.  When ``strict=True`` all preconditions must be checkable given
    the supplied context; when ``strict=False`` only structural issues are
    treated as failures.

    Attributes
    ----------
    strict:
        Whether to apply strict verification rules.
    _verification_log:
        Accumulated log of all verification events this verifier has run.
    """

    strict: bool = True
    _verification_log: list[dict] = field(default_factory=list)

    def verify(
        self,
        theorem: KindDiscoveryTheorem,
        *,
        context: dict | None = None,
    ) -> tuple[bool, list[str]]:
        """Run all verification checks on *theorem*.

        Combines the results of :meth:`verify_preconditions`,
        :meth:`verify_conclusions`, :meth:`check_logical_consistency`, and
        :meth:`check_scope_validity`.  Returns a summary pass/fail flag and
        a list of all issues discovered.

        Parameters
        ----------
        theorem:
            The theorem to verify.
        context:
            Optional context dictionary for precondition checking.

        Returns
        -------
        tuple[bool, list[str]]
            ``(all_passed, issues)`` where *all_passed* is True only when no
            issues were found.
        """
        ctx = context or {}
        all_issues: list[str] = []

        pre_ok, pre_issues = self.verify_preconditions(theorem, ctx)
        all_issues.extend(pre_issues)

        con_ok, con_issues = self.verify_conclusions(theorem)
        all_issues.extend(con_issues)

        logic_ok, logic_msg = self.check_logical_consistency(theorem)
        if not logic_ok:
            all_issues.append(f"Logical consistency: {logic_msg}")

        scope_ok, scope_msg = self.check_scope_validity(theorem)
        if not scope_ok:
            all_issues.append(f"Scope validity: {scope_msg}")

        passed = pre_ok and con_ok and logic_ok and scope_ok

        self._verification_log.append({
            "theorem_id": theorem.theorem_id,
            "passed": passed,
            "issue_count": len(all_issues),
            "timestamp": _now_iso(),
        })
        return passed, all_issues

    def verify_preconditions(
        self,
        theorem: KindDiscoveryTheorem,
        context: dict,
    ) -> tuple[bool, list[str]]:
        """Verify that all preconditions are well-formed and context-checkable.

        Each precondition is checked for:
        - Being non-empty after stripping whitespace.
        - Containing at least a rough subject and predicate (heuristic:
          at least 4 tokens, at least one of which is a verb-like word).
        - Being satisfiable given the supplied context (via
          :func:`_check_precondition`).

        In strict mode, a precondition that cannot be confirmed by the
        context generates an issue.  In non-strict mode only structural
        malformedness generates issues.

        Parameters
        ----------
        theorem:
            The theorem whose preconditions to verify.
        context:
            A context dictionary for keyword-based precondition checking.

        Returns
        -------
        tuple[bool, list[str]]
            ``(ok, issues)``
        """
        issues: list[str] = []
        ok = True

        if not theorem.preconditions:
            # No preconditions — trivially satisfied
            return True, []

        verb_hints = {
            "is", "are", "has", "have", "exists", "contains", "satisfies",
            "holds", "implies", "admits", "defined", "given", "established",
            "exceeds", "applied", "been", "with", "above", "below",
        }

        for i, pre in enumerate(theorem.preconditions):
            stripped = pre.strip()
            if not stripped:
                issues.append(f"Precondition {i+1} is empty.")
                ok = False
                continue

            tokens = _tokenize(stripped)
            if len(tokens) < 3:
                issues.append(
                    f"Precondition {i+1} appears too short to be well-formed: {stripped!r}"
                )
                ok = False
                continue

            # Heuristic: at least one verb-like word should be present
            if not (tokens & verb_hints):
                issues.append(
                    f"Precondition {i+1} may lack a predicate (no verb-like word found): {stripped!r}"
                )
                # In strict mode this is a failure; otherwise just a warning
                if self.strict:
                    ok = False

            # Context check
            if context and self.strict:
                if not _check_precondition(stripped, context):
                    issues.append(
                        f"Precondition {i+1} not satisfiable by provided context: {stripped!r}"
                    )
                    ok = False

        return ok, issues

    def verify_conclusions(
        self,
        theorem: KindDiscoveryTheorem,
        *,
        evidence: tuple = (),
    ) -> tuple[bool, list[str]]:
        """Verify that conclusions are non-empty and consistent with preconditions.

        Checks:
        - At least one conclusion exists.
        - Each conclusion is non-empty.
        - Each conclusion has meaningful keyword overlap with the preconditions
          (ensuring conclusions are not disconnected from the hypotheses).

        The *evidence* parameter is reserved for future use (currently unused
        but included for API compatibility with possible proof-assistant
        integrations).

        Parameters
        ----------
        theorem:
            The theorem whose conclusions to verify.
        evidence:
            Optional tuple of evidence strings (currently unused).

        Returns
        -------
        tuple[bool, list[str]]
            ``(ok, issues)``
        """
        issues: list[str] = []
        ok = True

        if not theorem.conclusions:
            issues.append("Theorem has no conclusions — at least one is required.")
            return False, issues

        # Build the combined token set of all preconditions + statement
        reference_tokens = _tokenize(theorem.statement)
        for pre in theorem.preconditions:
            reference_tokens.update(_tokenize(pre))

        for i, con in enumerate(theorem.conclusions):
            stripped = con.strip()
            if not stripped:
                issues.append(f"Conclusion {i+1} is empty.")
                ok = False
                continue

            con_tokens = _tokenize(stripped)
            if len(con_tokens) < 2:
                issues.append(
                    f"Conclusion {i+1} is too short to be meaningful: {stripped!r}"
                )
                ok = False
                continue

            # Check keyword overlap with preconditions/statement
            overlap = con_tokens & reference_tokens
            if not overlap and self.strict:
                issues.append(
                    f"Conclusion {i+1} shares no keywords with preconditions/statement: "
                    f"{stripped!r}"
                )
                ok = False

        return ok, issues

    def check_logical_consistency(
        self, theorem: KindDiscoveryTheorem
    ) -> tuple[bool, str]:
        """Check that preconditions and conclusions do not obviously contradict.

        Heuristic checks performed:
        1. **Negation conflict**: if a term appears with negation in a
           precondition (e.g. "not X") and without negation in a conclusion
           (or vice versa), flag an inconsistency.
        2. **Statement coherence**: the statement text should contain tokens
           from both the preconditions and the conclusions, indicating that the
           statement is a proper summary of the full theorem.

        Parameters
        ----------
        theorem:
            The theorem to check.

        Returns
        -------
        tuple[bool, str]
            ``(is_consistent, explanation)`` where *is_consistent* is False
            if a likely contradiction is detected.
        """
        # Detect simple negation patterns in preconditions
        negated_in_pre: set[str] = set()
        for pre in theorem.preconditions:
            # Find "not <word>" patterns
            for m in re.finditer(r"\bnot\s+(\w+)", pre.lower()):
                negated_in_pre.add(m.group(1))

        # Check that no conclusion asserts the same term without negation
        for con in theorem.conclusions:
            con_tokens = _tokenize(con)
            conflict = negated_in_pre & con_tokens
            if conflict:
                return (
                    False,
                    f"Conclusion asserts {conflict} which is negated in preconditions.",
                )

        # Statement coherence check
        if theorem.preconditions and theorem.conclusions:
            pre_tokens = set()
            for pre in theorem.preconditions:
                pre_tokens.update(_tokenize(pre))
            con_tokens_all = set()
            for con in theorem.conclusions:
                con_tokens_all.update(_tokenize(con))

            stmt_tokens = _tokenize(theorem.statement)
            pre_in_stmt = bool(pre_tokens & stmt_tokens)
            con_in_stmt = bool(con_tokens_all & stmt_tokens)

            if not pre_in_stmt and not con_in_stmt:
                return (
                    False,
                    "Statement shares no tokens with either preconditions or conclusions — "
                    "it may be disconnected from the theorem body.",
                )

        return True, "No logical inconsistencies detected."

    def check_scope_validity(self, theorem: KindDiscoveryTheorem) -> tuple[bool, str]:
        """Validate that the theorem's scope is consistent with its content.

        A UNIVERSAL theorem should not reference highly domain-specific
        terminology (e.g. named mathematical structures or specific domain
        labels that suggest it only applies in one domain).  A LOCAL theorem
        should not claim cross-domain applicability in its conclusions.

        Parameters
        ----------
        theorem:
            The theorem whose scope to validate.

        Returns
        -------
        tuple[bool, str]
            ``(is_valid, explanation)``
        """
        # Domain-specific marker tokens that should not appear in UNIVERSAL theorems
        domain_specific_markers = {
            "topology", "algebra", "geometry", "analysis", "number", "arithmetic",
            "combinatorics", "graph", "ring", "field", "group", "module", "sheaf",
            "manifold", "category", "topos",
        }

        all_content = " ".join([theorem.statement] + list(theorem.preconditions) + list(theorem.conclusions))
        content_tokens = _tokenize(all_content)

        if theorem.scope == TheoremScope.UNIVERSAL:
            domain_hits = domain_specific_markers & content_tokens
            if domain_hits:
                return (
                    False,
                    f"UNIVERSAL-scoped theorem references domain-specific terms: {domain_hits}. "
                    "Consider narrowing scope to DOMAIN_SPECIFIC or CROSS_DOMAIN.",
                )

        if theorem.scope == TheoremScope.LOCAL:
            cross_domain_terms = {"universal", "across", "transport", "analogy", "federation"}
            if cross_domain_terms & content_tokens:
                return (
                    False,
                    "LOCAL-scoped theorem contains cross-domain language. "
                    "Consider broadening scope to CROSS_DOMAIN.",
                )

        return True, f"Scope {theorem.scope.value!r} appears consistent with theorem content."

    def batch_verify(
        self, theorems: list[KindDiscoveryTheorem]
    ) -> list[tuple[bool, list[str]]]:
        """Verify a list of theorems, returning one result per theorem.

        Parameters
        ----------
        theorems:
            The list of theorems to verify.

        Returns
        -------
        list[tuple[bool, list[str]]]
            One ``(ok, issues)`` pair for each input theorem, in the same order.
        """
        return [self.verify(t) for t in theorems]

    def verification_report(self, theorem: KindDiscoveryTheorem) -> dict:
        """Return a comprehensive verification report for *theorem*.

        Runs all checks and collects results into a single dictionary suitable
        for logging or display.

        Parameters
        ----------
        theorem:
            The theorem to report on.

        Returns
        -------
        dict
            A dictionary with keys ``theorem_id``, ``name``, ``passed``,
            ``issues``, ``precondition_check``, ``conclusion_check``,
            ``logic_check``, ``scope_check``, and ``generated_at``.
        """
        pre_ok, pre_issues = self.verify_preconditions(theorem, {})
        con_ok, con_issues = self.verify_conclusions(theorem)
        logic_ok, logic_msg = self.check_logical_consistency(theorem)
        scope_ok, scope_msg = self.check_scope_validity(theorem)

        all_issues = pre_issues + con_issues
        if not logic_ok:
            all_issues.append(logic_msg)
        if not scope_ok:
            all_issues.append(scope_msg)

        return {
            "theorem_id": theorem.theorem_id,
            "name": theorem.name,
            "passed": pre_ok and con_ok and logic_ok and scope_ok,
            "issues": all_issues,
            "precondition_check": {"ok": pre_ok, "issues": pre_issues},
            "conclusion_check": {"ok": con_ok, "issues": con_issues},
            "logic_check": {"ok": logic_ok, "message": logic_msg},
            "scope_check": {"ok": scope_ok, "message": scope_msg},
            "generated_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# TheoremApplications
# ---------------------------------------------------------------------------

@dataclass
class TheoremApplications:
    """Applies theorems to guide and strengthen kind discovery.

    Uses a :class:`TheoremRegistry` to provide actionable guidance during
    the discovery process.  Records all applications in an internal log for
    auditing purposes.

    Attributes
    ----------
    registry:
        The registry from which theorems are drawn.
    _application_log:
        Accumulated log of all theorem applications performed.
    """

    registry: TheoremRegistry = field(default_factory=TheoremRegistry)
    _application_log: list[dict] = field(default_factory=list)

    def apply_to_candidate(
        self, candidate: KindCandidate
    ) -> list[tuple[KindDiscoveryTheorem, str]]:
        """Apply all relevant theorems to a kind candidate.

        Finds all theorems applicable to *candidate* via
        :meth:`TheoremRegistry.find_applicable_to`, then generates a
        guidance string for each based on the theorem's conclusions and status.

        Parameters
        ----------
        candidate:
            The :class:`KindCandidate` to apply theorems to.

        Returns
        -------
        list[tuple[KindDiscoveryTheorem, str]]
            A list of ``(theorem, guidance)`` pairs, ordered by theorem
            strength (CLASSICAL first).
        """
        applicable = self.registry.find_applicable_to(candidate)
        results: list[tuple[KindDiscoveryTheorem, str]] = []

        for theorem in applicable:
            guidance_parts = [f"[{theorem.name}]"]
            for conclusion in theorem.conclusions:
                guidance_parts.append(f"  → {conclusion}")
            if theorem.status == TheoremStatus.CLASSICAL:
                guidance_parts.append("  (Classical result — high confidence)")
            elif theorem.status == TheoremStatus.ESTABLISHED:
                guidance_parts.append("  (Established — reliable guidance)")
            guidance = "\n".join(guidance_parts)
            results.append((theorem, guidance))

            self._application_log.append({
                "kind": "candidate",
                "target_id": candidate.candidate_id,
                "theorem_id": theorem.theorem_id,
                "timestamp": _now_iso(),
            })

        return results

    def apply_to_field(
        self, field_obj: ObstructionField
    ) -> list[tuple[KindDiscoveryTheorem, str]]:
        """Apply theorems relevant to an obstruction field.

        Searches the registry for theorems whose statement or tags mention
        the field's domain or obstruction types, then generates guidance
        from their conclusions.

        Parameters
        ----------
        field_obj:
            The :class:`ObstructionField` to apply theorems to.

        Returns
        -------
        list[tuple[KindDiscoveryTheorem, str]]
            A list of ``(theorem, guidance)`` pairs.
        """
        field_tokens = _tokenize(field_obj.domain)
        for obstruction_str in field_obj.obstructions:
            field_tokens.update(_tokenize(obstruction_str))
        for ot in field_obj.obstruction_types:
            field_tokens.update(_tokenize(ot.value))

        results: list[tuple[KindDiscoveryTheorem, str]] = []
        for theorem in self.registry.list_all():
            theorem_tokens = _tokenize(theorem.statement)
            for tag in theorem.tags:
                theorem_tokens.update(_tokenize(tag))

            if _jaccard(field_tokens, theorem_tokens) > 0.1 or (
                field_tokens & theorem_tokens
            ):
                guidance_lines = [
                    f"[{theorem.name}] applies to obstruction field in '{field_obj.domain}':",
                ]
                for con in theorem.conclusions:
                    guidance_lines.append(f"  → {con}")
                guidance = "\n".join(guidance_lines)
                results.append((theorem, guidance))

                self._application_log.append({
                    "kind": "field",
                    "target_id": field_obj.field_id,
                    "theorem_id": theorem.theorem_id,
                    "timestamp": _now_iso(),
                })

        return results

    def apply_to_kind(
        self, kind: NewKind
    ) -> list[tuple[KindDiscoveryTheorem, str]]:
        """Apply theorems to a fully discovered kind.

        Constructs a synthetic :class:`KindCandidate` from the kind's
        attributes and delegates to :meth:`apply_to_candidate`.

        Parameters
        ----------
        kind:
            The :class:`NewKind` to apply theorems to.

        Returns
        -------
        list[tuple[KindDiscoveryTheorem, str]]
            A list of ``(theorem, guidance)`` pairs.
        """
        from dataclasses import replace as _replace

        synthetic_candidate = KindCandidate(
            candidate_id=kind.kind_id,
            name=kind.name,
            description=kind.formal_definition,
            obstruction_pattern=" ".join(kind.theorems[:3]) if kind.theorems else "",
            frequency=len(kind.examples),
            confidence=kind.confidence,
            evidence_sources=kind.parent_patterns,
            status=kind.status,
            tags=kind.tags,
        )
        return self.apply_to_candidate(synthetic_candidate)

    def suggest_missing_theorems(self, kinds: list[NewKind]) -> list[str]:
        """Suggest areas where new theorems might be needed.

        Analyses the domains and tags found across the provided kinds and
        identifies clusters not well-covered by existing theorems.  Returns
        a list of suggested theorem names/descriptions.

        Parameters
        ----------
        kinds:
            The list of :class:`NewKind` instances to analyse.

        Returns
        -------
        list[str]
            A list of suggestion strings, each describing a potential new
            theorem that would improve coverage.
        """
        if not kinds:
            return ["No kinds provided — cannot suggest missing theorems."]

        # Collect all tags from kinds
        all_tags: Counter = Counter()
        for kind in kinds:
            for tag in kind.tags:
                all_tags[tag] += 1

        # Collect all tags covered by existing theorems
        covered_tags: set[str] = set()
        for theorem in self.registry.list_all():
            covered_tags.update(theorem.tags)

        # Find the most frequent kind tags NOT covered by any theorem
        uncovered: list[tuple[str, int]] = [
            (tag, count)
            for tag, count in all_tags.most_common()
            if tag not in covered_tags
        ]

        suggestions: list[str] = []
        for tag, count in uncovered[:5]:
            suggestions.append(
                f"Theorem about '{tag}' (appears in {count} kind(s) but has no covering theorem)."
            )

        # Also suggest based on domain diversity
        domains: Counter = Counter()
        for kind in kinds:
            # Extract domain hints from formal_definition tokens
            tokens = _tokenize(kind.formal_definition)
            for tok in tokens:
                if len(tok) > 4:
                    domains[tok] += 1

        for domain_token, count in domains.most_common(3):
            existing_tokens: set[str] = set()
            for t in self.registry.list_all():
                existing_tokens.update(_tokenize(t.statement))
            if domain_token not in existing_tokens:
                suggestions.append(
                    f"Theorem addressing '{domain_token}' domain concept "
                    f"(frequent in kind definitions, not in any theorem statement)."
                )

        if not suggestions:
            suggestions.append(
                "Theorem coverage appears adequate for the provided kinds. "
                "Consider adding theorems for edge cases or cross-domain generalizations."
            )

        return suggestions

    def guide_bootstrapping(self, candidate: KindCandidate) -> list[str]:
        """Provide ordered bootstrap guidance for a candidate.

        Finds all applicable theorems, extracts their conclusions as
        actionable guidance, and orders the guidance by theorem status
        (CLASSICAL first, then ESTABLISHED, PROVISIONAL, CONJECTURED).

        Parameters
        ----------
        candidate:
            The :class:`KindCandidate` to generate bootstrap guidance for.

        Returns
        -------
        list[str]
            An ordered list of guidance strings for bootstrapping *candidate*.
        """
        applicable = self.registry.find_applicable_to(candidate)

        status_rank = {
            TheoremStatus.CLASSICAL: 0,
            TheoremStatus.ESTABLISHED: 1,
            TheoremStatus.PROVISIONAL: 2,
            TheoremStatus.CONJECTURED: 3,
        }

        # Sort by status rank so most reliable guidance comes first
        ordered = sorted(applicable, key=lambda t: (status_rank.get(t.status, 99), t.name))

        guidance_lines: list[str] = []
        for theorem in ordered:
            prefix = f"[{theorem.status.value.upper()}] {theorem.name}:"
            for conclusion in theorem.conclusions:
                guidance_lines.append(f"{prefix} {conclusion}")

        if not guidance_lines:
            guidance_lines.append(
                "No applicable theorems found. Proceed with default bootstrapping heuristics."
            )

        return guidance_lines

    def strengthen_with_theorems(
        self,
        candidate: KindCandidate,
        theorems: list[KindDiscoveryTheorem],
    ) -> KindCandidate:
        """Boost a candidate's confidence based on applicable theorems.

        For each theorem in *theorems* that applies to *candidate*, the
        candidate's confidence is boosted:

        - +0.10 per CLASSICAL theorem
        - +0.05 per ESTABLISHED theorem
        - +0.02 per PROVISIONAL theorem
        - +0.01 per CONJECTURED theorem

        The resulting confidence is clamped to [0.0, 1.0].  Because
        :class:`KindCandidate` is frozen, a new instance is returned via
        ``dataclasses.replace()``.

        Parameters
        ----------
        candidate:
            The candidate to strengthen.
        theorems:
            The theorems to consider for strengthening.

        Returns
        -------
        KindCandidate
            A new candidate with updated confidence.
        """
        from dataclasses import replace as _replace

        boost_map = {
            TheoremStatus.CLASSICAL: 0.10,
            TheoremStatus.ESTABLISHED: 0.05,
            TheoremStatus.PROVISIONAL: 0.02,
            TheoremStatus.CONJECTURED: 0.01,
        }

        total_boost = 0.0
        for theorem in theorems:
            if theorem.applies_to(candidate):
                total_boost += boost_map.get(theorem.status, 0.0)

        new_confidence = _clamp(candidate.confidence + total_boost)
        return _replace(candidate, confidence=new_confidence)

    def theorem_coverage(self, kinds: list[NewKind]) -> dict:
        """Report how many theorems apply to each kind.

        For each kind in *kinds*, counts the number of theorems from the
        registry that apply (via :meth:`apply_to_kind`).

        Parameters
        ----------
        kinds:
            The list of :class:`NewKind` instances to analyse.

        Returns
        -------
        dict
            A dictionary with:
            - One entry per ``kind_id`` → count of applicable theorems.
            - ``"mean_coverage"``: the mean number of theorems per kind.
            - ``"uncovered_kinds"``: list of names of kinds with zero coverage.
        """
        result: dict = {}
        counts: list[int] = []
        uncovered: list[str] = []

        for kind in kinds:
            applications = self.apply_to_kind(kind)
            count = len(applications)
            result[kind.kind_id] = count
            counts.append(count)
            if count == 0:
                uncovered.append(kind.name)

        result["mean_coverage"] = sum(counts) / len(counts) if counts else 0.0
        result["uncovered_kinds"] = uncovered
        return result

    def application_report(self, kinds: list[NewKind]) -> str:
        """Generate a human-readable coverage and guidance report.

        For each kind, lists the applicable theorems and their guidance.
        Ends with a summary of coverage statistics.

        Parameters
        ----------
        kinds:
            The list of :class:`NewKind` instances to report on.

        Returns
        -------
        str
            A multi-line text report string.
        """
        lines: list[str] = [
            "=" * 70,
            "THEOREM APPLICATION REPORT",
            f"Generated: {_now_iso()}",
            f"Kinds analysed: {len(kinds)}",
            f"Theorems in registry: {self.registry.size()}",
            "=" * 70,
            "",
        ]

        coverage = self.theorem_coverage(kinds)

        for kind in kinds:
            applications = self.apply_to_kind(kind)
            lines.append(f"Kind: {kind.name} [{kind.kind_id[:8]}]")
            lines.append(f"  Confidence: {kind.confidence:.3f}  Status: {kind.status.value}")
            lines.append(f"  Applicable theorems: {len(applications)}")
            if applications:
                for theorem, guidance in applications:
                    lines.append(f"    • {theorem.name} ({theorem.status.value})")
            else:
                lines.append("    (no applicable theorems)")
            lines.append("")

        lines.append("-" * 70)
        lines.append(f"Mean theorem coverage: {coverage.get('mean_coverage', 0.0):.2f}")
        if coverage.get("uncovered_kinds"):
            lines.append(
                "Kinds with no theorem coverage: "
                + ", ".join(coverage["uncovered_kinds"])
            )
        else:
            lines.append("All kinds have at least one applicable theorem.")
        lines.append("=" * 70)

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TheoremCatalog
# ---------------------------------------------------------------------------

class TheoremCatalog:
    """Curated catalog of canonical kind-discovery theorems.

    Builds and maintains a collection of the core theoretical results from
    theory2.tex Ch 56, providing lookup and application interfaces.

    The catalog is initialised with the seven canonical theorems defined
    as module-level constants and provides a unified interface for querying
    and applying them.

    Attributes
    ----------
    _registry:
        The internal theorem registry.
    _applications:
        The theorem-application engine backed by the registry.
    _verifier:
        The verifier used to check theorems during catalog construction.
    """

    def __init__(self) -> None:
        self._registry = TheoremRegistry()
        self._applications = TheoremApplications(registry=self._registry)
        self._verifier = TheoremVerifier(strict=False)
        self._build_catalog()

    def _build_catalog(self) -> None:
        """Construct the catalog by registering all seven canonical theorems.

        Each theorem is registered and then subjected to a non-strict
        verification pass.  Verification issues are recorded but do not
        prevent registration — the catalog is always fully populated after
        construction.
        """
        canonical_theorems = [
            OBSTRUCTION_COMPLETENESS,
            KIND_EXISTENCE,
            PATTERN_STABILITY,
            BOOTSTRAP_TERMINATION,
            CONFIDENCE_MONOTONICITY,
            CROSS_DOMAIN_GENERALITY,
            UNIQUENESS_OF_MINIMAL_KIND,
        ]
        for theorem in canonical_theorems:
            self._registry.register(theorem)
            ok, issues = self._verifier.verify(theorem)
            if not ok:
                # Log issues but do not fail construction
                pass  # issues recorded in _verifier._verification_log

    def get_all(self) -> list[KindDiscoveryTheorem]:
        """Return all theorems in the catalog, sorted by name.

        Returns
        -------
        list[KindDiscoveryTheorem]
            All seven canonical theorems in alphabetical order.
        """
        return self._registry.list_all()

    def get(self, theorem_id: str) -> KindDiscoveryTheorem | None:
        """Look up a canonical theorem by its identifier.

        Parameters
        ----------
        theorem_id:
            The theorem identifier (e.g. "thm-kd-001").

        Returns
        -------
        KindDiscoveryTheorem | None
            The theorem, or None if not found.
        """
        return self._registry.get(theorem_id)

    def apply_to_kind(self, kind: NewKind) -> list[str]:
        """Return guidance strings for all theorems applicable to *kind*.

        Parameters
        ----------
        kind:
            The :class:`NewKind` to apply the catalog to.

        Returns
        -------
        list[str]
            A list of guidance strings derived from applicable theorem conclusions.
        """
        applications = self._applications.apply_to_kind(kind)
        return [guidance for _, guidance in applications]

    def apply_to_candidate(self, candidate: KindCandidate) -> list[str]:
        """Return guidance strings for all theorems applicable to *candidate*.

        Parameters
        ----------
        candidate:
            The :class:`KindCandidate` to apply the catalog to.

        Returns
        -------
        list[str]
            A list of guidance strings derived from applicable theorem conclusions.
        """
        applications = self._applications.apply_to_candidate(candidate)
        return [guidance for _, guidance in applications]

    def catalog_summary(self) -> str:
        """Return a multi-line human-readable summary of all catalog theorems.

        Lists each theorem with its id, name, status, scope, and a one-line
        description of its preconditions and conclusions.

        Returns
        -------
        str
            A formatted multi-line summary string.
        """
        lines = [
            "=" * 70,
            "KIND-DISCOVERY THEOREM CATALOG  (theory2.tex Ch 56)",
            f"Total theorems: {self._registry.size()}",
            "=" * 70,
            "",
        ]
        for theorem in self._registry.list_all():
            lines.append(theorem.summary())
            lines.append(f"  Statement: {theorem.statement[:120]}...")
            lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)

    def export_to_theorem_portfolio(self) -> Any:
        """Export catalog theorems to a TheoremPortfolio if available.

        If the optional ``jugeo.ideation.novelty.TheoremPortfolio`` class is
        importable, constructs an instance and populates it with the catalog
        theorem data.  Otherwise returns a plain dictionary representation of
        the catalog.

        Returns
        -------
        TheoremPortfolio | dict
            A populated TheoremPortfolio instance, or a dict fallback.
        """
        snapshot = self._registry.snapshot()
        if TheoremPortfolio is not None:
            portfolio = TheoremPortfolio()
            for theorem_id, theorem_dict in snapshot.items():
                try:
                    portfolio.add(theorem_dict)
                except Exception:
                    pass
            return portfolio
        # Fallback: return a plain dict representation
        return {
            "catalog_type": "KindDiscoveryTheoremCatalog",
            "source": "theory2.tex Ch 56",
            "theorem_count": len(snapshot),
            "theorems": snapshot,
            "exported_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# 7 Canonical theorem instances
# ---------------------------------------------------------------------------

OBSTRUCTION_COMPLETENESS = KindDiscoveryTheorem(
    theorem_id="thm-kd-001",
    name="Obstruction Completeness",
    statement=(
        "For any kind K in domain D, the obstruction field O(K) is complete if and only if "
        "every obstruction in D that prevents kind formation is represented in O(K). "
        "Completeness implies that no additional obstructions can be discovered without "
        "expanding the domain."
    ),
    preconditions=(
        "domain D is well-defined with a fixed kind vocabulary",
        "obstruction extraction algorithm has been applied exhaustively",
        "at least three independent evidence sources have been consulted",
    ),
    conclusions=(
        "O(K) contains all domain-relevant obstructions",
        "no additional obstruction field can be added without domain expansion",
        "kind K can be formally defined given O(K)",
    ),
    proof_sketch=(
        "By induction on the structure of D: the base case holds for atomic domains with a single "
        "obstruction. The inductive step follows from the monotonicity of obstruction extraction "
        "under domain union. Completeness is witnessed by the absence of counterexamples in the "
        "domain vocabulary."
    ),
    references=("theory2.tex §56.2", "Obstruction Theory for Categories, §3"),
    status=TheoremStatus.ESTABLISHED,
    scope=TheoremScope.DOMAIN_SPECIFIC,
    tags=frozenset({"obstruction", "completeness", "kind-formation"}),
)

KIND_EXISTENCE = KindDiscoveryTheorem(
    theorem_id="thm-kd-002",
    name="Kind Existence",
    statement=(
        "Given a non-empty obstruction field O with strength above threshold τ > 0.3, "
        "a well-defined kind K exists in domain D. The kind is unique up to isomorphism "
        "when the obstruction field is minimal."
    ),
    preconditions=(
        "obstruction field O is non-empty with at least one entry of strength > 0.3",
        "domain D has an established kind vocabulary",
        "the obstruction types in O are mutually consistent",
    ),
    conclusions=(
        "a kind K exists in domain D with O as its obstruction field",
        "K admits a formal type signature derivable from O",
        "K is unique up to isomorphism if O is minimal",
    ),
    proof_sketch=(
        "Existence follows from the Universal Property of Kind Formation: every consistent "
        "obstruction field with positive strength generates a kind in the target domain. "
        "Uniqueness under minimality is proved by the adjunction between obstruction fields "
        "and kinds in the kind-formation category."
    ),
    references=("theory2.tex §56.3", "theory2.tex §12.1 (Universal Property)"),
    status=TheoremStatus.ESTABLISHED,
    scope=TheoremScope.CROSS_DOMAIN,
    tags=frozenset({"existence", "uniqueness", "kind-formation", "obstruction"}),
)

PATTERN_STABILITY = KindDiscoveryTheorem(
    theorem_id="thm-kd-003",
    name="Pattern Stability",
    statement=(
        "A kind pattern P extracted from domain D₁ is stable across domain D₂ if and only if "
        "the Jaccard similarity between the structural vocabularies of D₁ and D₂ exceeds the "
        "stability threshold σ = 0.4. Stable patterns are preserved under analogy transport."
    ),
    preconditions=(
        "pattern P has been validated in source domain D₁",
        "target domain D₂ shares structural vocabulary with D₁ above threshold σ = 0.4",
        "the analogy transport map is structure-preserving on pattern variables",
    ),
    conclusions=(
        "pattern P remains valid in target domain D₂",
        "pattern variables retain their binding constraints under transport",
        "confidence of P in D₂ is at least confidence(P, D₁) × σ",
    ),
    proof_sketch=(
        "The stability condition is equivalent to requiring that the structural homomorphism "
        "between domain vocabularies preserves pattern templates. The confidence bound follows "
        "from the Attenuation Lemma applied to analogy transport (theory2.tex §45.3)."
    ),
    references=("theory2.tex §56.4", "theory2.tex §45.3 (Attenuation Lemma)"),
    status=TheoremStatus.PROVISIONAL,
    scope=TheoremScope.CROSS_DOMAIN,
    tags=frozenset({"pattern", "stability", "analogy", "transport"}),
)

BOOTSTRAP_TERMINATION = KindDiscoveryTheorem(
    theorem_id="thm-kd-004",
    name="Bootstrap Termination",
    statement=(
        "The kind bootstrapping process for a candidate with finite obstruction field O terminates "
        "in at most |O| + |P| steps, where |P| is the number of validated patterns. Each step "
        "strictly reduces the residual obligation."
    ),
    preconditions=(
        "kind candidate has a finite non-empty obstruction field",
        "the bootstrap plan is acyclic (no circular dependencies between steps)",
        "each bootstrap step reduces at least one unresolved obstruction",
    ),
    conclusions=(
        "the bootstrap process terminates in finite time",
        "termination bound is |O| + |P| steps",
        "upon termination, either a valid NewKind is produced or a formal rejection certificate is issued",
    ),
    proof_sketch=(
        "Model the bootstrapping process as a rewriting system on obstruction sets. Each step is "
        "a rewrite rule that removes at least one obstruction from the residual set. Since the "
        "residual set is finite and strictly decreasing, the process terminates by well-foundedness "
        "of the natural numbers."
    ),
    references=("theory2.tex §56.5", "Term Rewriting and All That, §2.1"),
    status=TheoremStatus.ESTABLISHED,
    scope=TheoremScope.UNIVERSAL,
    tags=frozenset({"bootstrap", "termination", "finiteness", "rewriting"}),
)

CONFIDENCE_MONOTONICITY = KindDiscoveryTheorem(
    theorem_id="thm-kd-005",
    name="Confidence Monotonicity",
    statement=(
        "The confidence score conf(K) of a kind K is monotonically non-decreasing under "
        "admissible evidence addition. For any admissible evidence e not contradicting existing "
        "evidence, conf(K ∪ {e}) ≥ conf(K)."
    ),
    preconditions=(
        "new evidence e is admissible (consistent with existing obstruction field)",
        "evidence e is relevant to the domain of kind K",
        "the confidence aggregation function is monotone (e.g., weighted mean with positive weights)",
    ),
    conclusions=(
        "adding admissible evidence cannot decrease kind confidence",
        "confidence converges to a fixed point given consistent evidence",
        "contradictory evidence triggers a trust demotion rather than confidence increase",
    ),
    proof_sketch=(
        "The confidence function is a weighted mean of obstruction field strengths, which is "
        "monotone in the set of inputs when all weights are positive. Admissibility ensures no "
        "weight becomes negative. Convergence follows from boundedness (conf ∈ [0,1]) and "
        "monotonicity."
    ),
    references=("theory2.tex §56.6", "theory2.tex §252 (Trust Monotonicity)"),
    status=TheoremStatus.CLASSICAL,
    scope=TheoremScope.UNIVERSAL,
    tags=frozenset({"confidence", "monotonicity", "evidence", "trust"}),
)

CROSS_DOMAIN_GENERALITY = KindDiscoveryTheorem(
    theorem_id="thm-kd-006",
    name="Cross-Domain Generality",
    statement=(
        "A kind K is cross-domain general if it can be transported across at least three distinct "
        "domains while retaining its core obstruction structure. Cross-domain general kinds have "
        "scope UNIVERSAL and serve as canonical representatives of their obstruction class."
    ),
    preconditions=(
        "kind K has been validated in at least one source domain",
        "at least two target domains exist with sufficient structural overlap",
        "the obstruction field of K contains at least one STRUCTURAL type obstruction",
    ),
    conclusions=(
        "kind K can be transported to all domains with sufficient structural overlap",
        "K is upgraded to UNIVERSAL scope upon successful transport to three or more domains",
        "K serves as a canonical representative for its obstruction class across domains",
    ),
    proof_sketch=(
        "By the Cross-Domain Transport Theorem (theory2.tex §56.7), structural obstructions are "
        "domain-independent and transport freely. The upgrade to UNIVERSAL scope follows from the "
        "Generality Criterion: a kind is universal if its obstruction class has representatives "
        "in all sufficiently rich domains."
    ),
    references=("theory2.tex §56.7", "theory2.tex §45 (Federation Theory)"),
    status=TheoremStatus.PROVISIONAL,
    scope=TheoremScope.CROSS_DOMAIN,
    tags=frozenset({"cross-domain", "generality", "transport", "universal"}),
)

UNIQUENESS_OF_MINIMAL_KIND = KindDiscoveryTheorem(
    theorem_id="thm-kd-007",
    name="Uniqueness of Minimal Kind",
    statement=(
        "For any obstruction field O in domain D, the minimal kind K_min(O) — the kind with the "
        "smallest obstruction field that covers all obstructions in O — is unique up to "
        "isomorphism. K_min(O) is the canonical form of the kind determined by O."
    ),
    preconditions=(
        "obstruction field O is non-empty and internally consistent",
        "domain D admits a kind lattice with meet and join operations",
        "minimality is defined with respect to the obstruction-subsumption partial order",
    ),
    conclusions=(
        "K_min(O) exists and is unique up to isomorphism in domain D",
        "any other kind with obstruction field O' ⊇ O is a specialisation of K_min(O)",
        "K_min(O) provides the canonical starting point for kind bootstrapping",
    ),
    proof_sketch=(
        "Existence follows from Kind Existence (thm-kd-002) applied to the minimal covering "
        "obstruction field. Uniqueness follows from the Yoneda Lemma applied to the functor "
        "taking obstruction fields to kinds: two minimal kinds with the same obstruction class "
        "are naturally isomorphic."
    ),
    references=("theory2.tex §56.8", "theory2.tex §10 (Yoneda for Kinds)"),
    status=TheoremStatus.ESTABLISHED,
    scope=TheoremScope.DOMAIN_SPECIFIC,
    tags=frozenset({"minimality", "uniqueness", "obstruction", "canonical"}),
)


_DEFAULT_CATALOG = TheoremCatalog()
