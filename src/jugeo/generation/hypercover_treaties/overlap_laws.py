"""Overlap law discovery and the OverlapLawLibrary.

theory2.tex §41.5 — "Overlap law induction and stabilization"

Overlap law discovery mines recurring behavioral compatibility patterns from
synthesis history and generalizes them into universal predicates that hold
across multiple patches.  A *law* is a predicate φ(s_i, s_j) on pairs of
local sections (s_i on patch U_i, s_j on patch U_j) asserting that their
restrictions to the intersection U_i ∩ U_j agree.

The induction procedure (theory2.tex §41.5, Algorithm 41.1) proceeds:

  1. Mine   — scan synthesis records for recurring (patch_pair, behavior)
              co-occurrences and build LawCandidate objects.
  2. Test   — each candidate is tested against the available evidence pool;
              counterexamples are recorded.
  3. Generalize — candidates are made progressively more abstract until
                  confidence drops below min_law_confidence or max
                  generalization depth is reached.
  4. Verify — surviving candidates are verified against the OverlapTreaty
              objects in the synthesis record to ensure no contradictions.
  5. Promote — verified candidates become OverlapLaw objects and are
               added to the OverlapLawLibrary.

Stabilization (theory2.tex §41.5.3) upgrades a law's LawStability as
additional synthesis records confirm it:
  - UNSTABLE   → PROVISIONAL once 1 supporting record is found
  - PROVISIONAL → STABLE     once min_support_count records confirm it
  - STABLE     → PROVEN      once a formal descent verification succeeds

This module provides:

* LawCandidate        — mutable working candidate before promotion
* LawVerifier         — verifies candidates against treaties and evidence
* OverlapLawDiscovery — orchestrates the mine/test/generalize/verify/promote pipeline
* OverlapLawLibrary   — curated, indexed collection of stable overlap laws

Helper functions:
* extract_predicate_keywords   — tokenises a predicate into searchable keywords
* predicates_compatible        — checks two predicates for contradictions
* compute_jaccard_similarity   — set-similarity metric for predicate comparison
* generalize_predicate         — makes a predicate more abstract
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.generation.hypercover_treaties.models import (
    DEFAULT_CONFIG,
    CandidateSource,
    HypercoverSynthesisRecord,
    LawStability,
    OverlapLaw,
    OverlapLawIndex,
    SynthesisConfig,
    SynthesisPhase,
    TreatyRole,
)

try:
    from jugeo.geometry.descent import (
        DescentEngine, DescentResult, LocalSection, OverlapCondition,
        GluingData, DescentObstruction, RepairFrontier, DescentStrategy, OverlapStatus,
    )
    from jugeo.geometry.covers import Cover
    from jugeo.geometry.supports import SupportRegion
    from jugeo.geometry.site import CoordinateObject, CoordinateKind
    from jugeo.generation.goals import (
        GenerationGoal, GoalDecomposer, ConstructionGoal, GoalPriority, GoalStatus, OverlapGoal,
    )
    from jugeo.generation.construction import (
        Candidate, ConstructionLoop, ConstructionResult, ConstructionContext,
    )
    from jugeo.generation.treaties import OverlapTreaty, TreatyClause, TreatyStatus, evaluate_treaty
    from jugeo.orchestration.frontier import FrontierNode, Frontier, FrontierItem
    from jugeo.evidence.trust import TrustTier, TrustLevel
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def extract_predicate_keywords(predicate: str) -> list[str]:
    """Tokenise *predicate* into a list of lowercase keyword strings.

    Words shorter than 3 characters are dropped (stop-word filtering).
    Common logical connectives ('and', 'or', 'not', 'for', 'any', 'all',
    'the', 'on', 'in', 'to', 'of', 'is', 'are') are also filtered out.

    Parameters
    ----------
    predicate:
        A natural-language predicate description such as
        "For any section on patch 'A' and patch 'B', restrictions agree."

    Returns
    -------
    list[str]
        Lowercase tokens useful for keyword matching and indexing.

    Examples
    --------
    >>> extract_predicate_keywords("sections on patch A and patch B agree")
    ['sections', 'patch', 'patch', 'agree']
    """
    stopwords = {
        "and", "or", "not", "for", "any", "all", "the", "on", "in", "to",
        "of", "is", "are", "a", "an", "that", "this", "their", "its",
        "with", "by", "at", "from", "do", "does", "be", "has", "have",
        "such", "if", "then", "else", "when", "where", "which", "who",
    }
    # Split on whitespace and punctuation
    raw_tokens = re.split(r"[\s\.,;:!?()\[\]{}'\"]+", predicate.lower())
    keywords = [
        tok
        for tok in raw_tokens
        if tok and len(tok) >= 3 and tok not in stopwords
    ]
    return keywords


def predicates_compatible(p1: str, p2: str) -> bool:
    """Check whether two predicate descriptions are mutually compatible.

    Two predicates are considered *incompatible* if one explicitly negates
    the other.  We detect this by checking for the presence of the token
    "not" immediately before a keyword that appears prominently in the
    other predicate.

    This is a lightweight heuristic; a complete compatibility check would
    require a formal logic solver.

    Parameters
    ----------
    p1, p2:
        Predicate description strings.

    Returns
    -------
    bool
        True iff the predicates are not detected as contradictory.

    Examples
    --------
    >>> predicates_compatible("sections agree on intersection", "sections agree on intersection")
    True
    >>> predicates_compatible("sections agree", "sections do not agree")
    False
    """
    kw1 = set(extract_predicate_keywords(p1))
    kw2 = set(extract_predicate_keywords(p2))

    def has_negation_of(base_pred: str, neg_pred: str) -> bool:
        """Return True if neg_pred is a negation of base_pred."""
        base_kw = set(extract_predicate_keywords(base_pred))
        neg_lower = neg_pred.lower()
        # Detect "not <keyword>" pattern where <keyword> is prominent in base
        for kw in base_kw:
            patterns = [f"not {kw}", f"do not {kw}", f"does not {kw}", f"never {kw}"]
            if any(pat in neg_lower for pat in patterns):
                return True
        return False

    if has_negation_of(p1, p2):
        return False
    if has_negation_of(p2, p1):
        return False

    # Two predicates with zero keyword overlap are trivially compatible
    # (they speak of entirely different things)
    if not kw1.intersection(kw2):
        return True

    # Check Jaccard similarity; very high similarity predicates that share
    # a strong negation indicator are incompatible
    jaccard = compute_jaccard_similarity(frozenset(kw1), frozenset(kw2))
    if jaccard > 0.8:
        # Very similar predicates — check for negation markers
        neg_markers = {"not", "never", "no", "none", "disagree", "conflict", "violate"}
        p1_has_neg = any(m in p1.lower().split() for m in neg_markers)
        p2_has_neg = any(m in p2.lower().split() for m in neg_markers)
        if p1_has_neg != p2_has_neg:
            return False  # One negates, one does not

    return True


def compute_jaccard_similarity(set1: frozenset[Any], set2: frozenset[Any]) -> float:
    """Compute the Jaccard similarity coefficient between two sets.

    J(A, B) = |A ∩ B| / |A ∪ B|

    Returns 0.0 for two empty sets (by convention) and 1.0 for identical
    non-empty sets.

    Parameters
    ----------
    set1, set2:
        Frozensets of comparable elements.

    Returns
    -------
    float
        Jaccard similarity in [0.0, 1.0].

    Examples
    --------
    >>> compute_jaccard_similarity(frozenset({'a', 'b'}), frozenset({'b', 'c'}))
    0.3333333333333333
    """
    if not set1 and not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    if union == 0:
        return 0.0
    return intersection / union


def generalize_predicate(predicate: str) -> str:
    """Make a predicate more abstract by replacing specific identifiers.

    Applies the following generalisation transformations (in order):

    1. Replace quoted patch names (e.g. ``'my_patch'``) with the placeholder
       ``<patch>``.
    2. Replace intersection notation like ``'A ∩ B'`` or ``'A_cap_B'`` with
       ``<intersection>``.
    3. Replace numeric literals with ``<n>``.
    4. Replace hash-like strings (32+ hex chars) with ``<id>``.
    5. Replace specific coordinate component strings (dotted paths) with
       ``<coordinate>``.

    Parameters
    ----------
    predicate:
        A predicate description string.

    Returns
    -------
    str
        A more abstract version of the predicate with specifics replaced
        by placeholders.

    Examples
    --------
    >>> generalize_predicate("sections on patch 'foo' and patch 'bar' agree")
    "sections on patch <patch> and patch <patch> agree"
    """
    result = predicate

    # 1. Replace quoted identifiers (likely patch names)
    result = re.sub(r"'[a-zA-Z_][a-zA-Z0-9_./\-]*'", "<patch>", result)
    result = re.sub(r'"[a-zA-Z_][a-zA-Z0-9_./\-]*"', "<patch>", result)

    # 2. Replace intersection notation
    result = re.sub(r"<patch>\s*[∩∧]\s*<patch>", "<intersection>", result)
    result = re.sub(r"<patch>_cap_<patch>", "<intersection>", result)
    result = re.sub(r"\w+\s*∩\s*\w+", "<intersection>", result)
    result = re.sub(r"\w+_cap_\w+", "<intersection>", result)

    # 3. Replace numeric literals
    result = re.sub(r"\b\d+(\.\d+)?\b", "<n>", result)

    # 4. Replace hex IDs (e.g. UUID fragments)
    result = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", result)

    # 5. Replace dotted coordinate paths
    result = re.sub(r"\b[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*){2,}\b", "<coordinate>", result)

    return result


# ---------------------------------------------------------------------------
# LawCandidate — mutable working object
# ---------------------------------------------------------------------------


@dataclass(init=False)
class LawCandidate:
    """A mutable working candidate for an overlap law.

    Unlike OverlapLaw (which is frozen and stable), a LawCandidate is
    mutable and undergoes testing, counterexample collection, and
    generalisation before being promoted to an OverlapLaw.

    The lifecycle is:
    1. Created by OverlapLawDiscovery.mine_patterns()
    2. Tested against evidence via test_against()
    3. Counterexamples collected via add_counterexample()
    4. Made more abstract via generalize()
    5. Promoted to OverlapLaw via to_overlap_law() once confidence is high enough

    Attributes
    ----------
    candidate_id:
        UUID identifying this candidate.
    predicate:
        Symbolic description of the law being proposed.
    support:
        List of evidence item IDs supporting this law.
    confidence:
        Estimated probability that this predicate is universally valid [0, 1].
    counterexamples:
        List of evidence item IDs that violate this predicate.
    patch_pair:
        The (patch_a, patch_b) pair this candidate governs.
    generalization_level:
        How many times this candidate has been generalised (via generalize()).
    """

    candidate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    predicate: str = ""
    support: list[str] = field(default_factory=list)
    confidence: float = 0.0
    counterexamples: list[str] = field(default_factory=list)
    patch_pair: tuple[str, str] = ("", "")
    generalization_level: int = 0
    description: str = ""

    def __init__(
        self,
        candidate_id: str | None = None,
        predicate: str = "",
        support: list[str] | None = None,
        confidence: float | None = None,
        counterexamples: list[str] | None = None,
        patch_pair: tuple[str, str] = ("", ""),
        generalization_level: int = 0,
        *,
        law_id: str | None = None,
        stability: float | None = None,
        evidence_count: int = 0,
        description: str = "",
    ) -> None:
        self.candidate_id = candidate_id or law_id or str(uuid.uuid4())
        self.predicate = predicate
        self.support = list(support or [])
        if evidence_count > len(self.support):
            self.support.extend(f"evidence-{idx}" for idx in range(len(self.support), evidence_count))
        self.confidence = float(confidence if confidence is not None else (stability if stability is not None else 0.0))
        self.counterexamples = list(counterexamples or [])
        self.patch_pair = patch_pair
        self.generalization_level = generalization_level
        self.description = description

    @property
    def law_id(self) -> str:
        return self.candidate_id

    @property
    def stability(self) -> float:
        return self.confidence

    @property
    def evidence_count(self) -> int:
        return len(self.support)

    def score(self) -> float:
        return self.confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "law_id": self.candidate_id,
            "predicate": self.predicate,
            "stability": self.confidence,
            "evidence_count": len(self.support),
            "description": self.description,
            "patch_pair": list(self.patch_pair),
            "generalization_level": self.generalization_level,
        }

    def test_against(self, evidence: dict[str, Any] | Any) -> bool:
        """Test whether *evidence* satisfies this candidate's predicate.

        Extracts keywords from the predicate and checks whether the evidence
        dict contains those keywords as keys or values.  Evidence satisfies
        the predicate when at least half the predicate keywords are present
        in the evidence's keys or string values.

        Parameters
        ----------
        evidence:
            A dict of observed facts.  Common keys: 'patch', 'section',
            'restriction', 'agreement', 'violation'.

        Returns
        -------
        bool
            True iff the evidence is consistent with this predicate.
        """
        if not self.predicate:
            return True  # Vacuously true

        keywords = extract_predicate_keywords(self.predicate)
        if not keywords:
            return True

        if isinstance(evidence, dict):
            evidence_values = evidence.values()
            evidence_keys = evidence.keys()
        else:
            evidence_mapping = getattr(evidence, "__dict__", None)
            if evidence_mapping is None and hasattr(evidence, "__dataclass_fields__"):
                evidence_mapping = {
                    field_name: getattr(evidence, field_name)
                    for field_name in evidence.__dataclass_fields__
                }
            if evidence_mapping is None:
                evidence_mapping = {"evidence": evidence}
            evidence_values = evidence_mapping.values()
            evidence_keys = evidence_mapping.keys()

        # Build a string corpus from the evidence
        evidence_str = " ".join(
            str(v) for v in evidence_values
        ).lower() + " " + " ".join(str(k) for k in evidence_keys).lower()

        matched = sum(1 for kw in keywords if kw in evidence_str)
        hit_rate = matched / len(keywords)

        # Check for explicit violation markers
        violation_markers = {"violat", "contradict", "disagree", "mismatch", "conflict"}
        for marker in violation_markers:
            if marker in evidence_str:
                # Evidence explicitly violates the predicate
                return False

        # Also check patch pair relevance
        patch_a, patch_b = self.patch_pair
        if patch_a and patch_b:
            if patch_a not in evidence_str and patch_b not in evidence_str:
                # Evidence is for a different patch pair — skip
                return True  # Neutral observation

        return hit_rate >= 0.5

    def generalize(self) -> "LawCandidate":
        """Return a new LawCandidate with the predicate made more general.

        Applies generalize_predicate() to the current predicate text and
        increments generalization_level.  The confidence is slightly reduced
        to reflect increased abstraction (more abstract = more uncertain).

        Returns
        -------
        LawCandidate
            A new mutable candidate (copy) with a generalised predicate.
        """
        new_predicate = generalize_predicate(self.predicate)
        confidence_decay = 0.05 * (self.generalization_level + 1)
        new_confidence = max(0.0, self.confidence - confidence_decay)
        return LawCandidate(
            candidate_id=self.candidate_id,
            predicate=new_predicate,
            support=list(self.support),
            confidence=new_confidence,
            counterexamples=list(self.counterexamples),
            patch_pair=self.patch_pair,
            generalization_level=self.generalization_level + 1,
        )

    def to_overlap_law(self) -> OverlapLaw:
        """Promote this candidate to a stable OverlapLaw.

        The resulting law's stability is:
        - UNSTABLE   if confidence < 0.4
        - PROVISIONAL if 0.4 ≤ confidence < 0.7
        - STABLE     if 0.7 ≤ confidence < 0.9
        - PROVEN     if confidence ≥ 0.9

        Support and violation counts are taken directly from the
        candidate's evidence lists.

        Returns
        -------
        OverlapLaw
            A frozen OverlapLaw ready for the library.
        """
        if self.confidence < 0.4:
            stability = LawStability.UNSTABLE
        elif self.confidence < 0.7:
            stability = LawStability.PROVISIONAL
        elif self.confidence < 0.9:
            stability = LawStability.STABLE
        else:
            stability = LawStability.PROVEN

        return OverlapLaw(
            law_id=self.candidate_id,
            patch_pair=self.patch_pair,
            predicate_description=self.predicate,
            stability=stability,
            support_count=len(self.support),
            violation_count=len(self.counterexamples),
            confidence=self.confidence,
        )

    def add_counterexample(self, ex: str) -> None:
        """Record *ex* as a counterexample.  Updates confidence in-place."""
        if ex not in self.counterexamples:
            self.counterexamples.append(ex)
        total = len(self.support) + len(self.counterexamples)
        self.confidence = len(self.support) / total if total > 0 else 0.0

    def add_support(self, ev: str) -> None:
        """Record *ev* as a supporting evidence item.  Updates confidence in-place."""
        if ev not in self.support:
            self.support.append(ev)
        total = len(self.support) + len(self.counterexamples)
        self.confidence = len(self.support) / total if total > 0 else 0.0

    def support_count(self) -> int:
        """Return the number of supporting evidence items."""
        return len(self.support)

    def counterexample_count(self) -> int:
        """Return the number of counterexamples."""
        return len(self.counterexamples)

    def observation_count(self) -> int:
        """Return total observations (support + counterexamples)."""
        return len(self.support) + len(self.counterexamples)

    def violation_rate(self) -> float:
        """Return the fraction of observations that are counterexamples."""
        total = self.observation_count()
        if total == 0:
            return 0.0
        return len(self.counterexamples) / total

    def __repr__(self) -> str:
        return (
            f"LawCandidate("
            f"id={self.candidate_id[:8]!r}, "
            f"pair={self.patch_pair!r}, "
            f"conf={self.confidence:.3f}, "
            f"support={self.support_count()}, "
            f"cex={self.counterexample_count()}, "
            f"gen={self.generalization_level})"
        )


# ---------------------------------------------------------------------------
# LawVerifier
# ---------------------------------------------------------------------------


class LawVerifier:
    """Verifies overlap laws against treaties and evidence pools.

    The verifier checks that a candidate OverlapLaw is:
    (a) compatible with all OverlapTreaty objects in a record (no
        treaty clause contradicts the law's predicate), and
    (b) consistent with an evidence pool (no evidence item is a
        counterexample to the law's predicate).

    theory2.tex §41.5.2 describes verification as the process of checking
    a law's predicate against the *ground truth* established by the descent
    datum.  Here we approximate this with string-level compatibility checks
    and keyword matching.
    """

    def __init__(self) -> None:
        self.provenance: tuple[str, ...] = ()

    def verify(
        self, law: OverlapLaw | Any, treaties: list[Any] | Any | None = None
    ) -> tuple[bool, list[str]] | bool:
        """Check *law* against a list of treaty objects.

        For each treaty in *treaties*, checks that the treaty's clauses
        (when accessible) do not contradict the law's predicate_description.

        Parameters
        ----------
        law:
            The OverlapLaw to verify.
        treaties:
            A list of OverlapTreaty objects, plain dicts, or any objects
            with a ``clauses`` attribute.

        Returns
        -------
        tuple[bool, list[str]]
            (verified, counterexamples) where *verified* is True iff no
            contradictions were found and *counterexamples* is a list of
            descriptions of the contradictions.
        """
        if isinstance(law, OverlapLaw):
            treaty_list = list(treaties) if isinstance(treaties, list) else [treaties] if treaties is not None else []
            counterexamples: list[str] = []

            for treaty in treaty_list:
                clauses = self._extract_clauses(treaty)
                for clause_text in clauses:
                    if not predicates_compatible(law.predicate_description, clause_text):
                        counterexamples.append(
                            f"Treaty clause contradicts law: "
                            f"law='{law.predicate_description[:60]}...', "
                            f"clause='{clause_text[:60]}...'"
                        )

            return len(counterexamples) == 0, counterexamples

        treaty = law
        candidate = treaties if isinstance(treaties, LawCandidate) else None
        self.provenance = tuple(getattr(treaty, "provenance", ()) or ())
        clauses = getattr(treaty, "clauses", ()) or ()
        if candidate is not None:
            return bool(candidate.test_against(treaty))
        if not clauses:
            return True

        return all(bool(getattr(clause, "satisfied", True)) for clause in clauses)

    def _extract_clauses(self, treaty: Any) -> list[str]:
        """Extract clause descriptions from a treaty object or dict."""
        if isinstance(treaty, dict):
            raw_clauses = treaty.get("clauses", [])
            result = []
            for c in raw_clauses:
                if isinstance(c, dict):
                    # TreatyClause-like dict with 'expectation' field
                    result.append(str(c.get("expectation", c.get("predicate", ""))))
                else:
                    result.append(str(c))
            return result

        # Try real OverlapTreaty
        try:
            clauses = treaty.clauses
            result = []
            for c in clauses:
                try:
                    # TreatyClause has field 'expectation' (NOT 'predicate')
                    result.append(str(c.expectation))
                except AttributeError:
                    result.append(str(c))
            return result
        except AttributeError:
            pass

        return []

    def find_counterexample(
        self, law: OverlapLaw, evidence_pool: list[dict[str, Any]]
    ) -> str | None:
        """Search *evidence_pool* for a dict that contradicts the law's predicate.

        An evidence item contradicts the law if ``test_against()`` via a
        temporary LawCandidate returns False.

        Parameters
        ----------
        law:
            The law to test.
        evidence_pool:
            List of evidence dicts.

        Returns
        -------
        str | None
            A string description of the first counterexample found, or None.
        """
        probe = LawCandidate(
            predicate=law.predicate_description,
            patch_pair=law.patch_pair,
        )
        for evidence in evidence_pool:
            if not probe.test_against(evidence):
                # Found a counterexample
                patch_a, patch_b = law.patch_pair
                return (
                    f"Evidence item with keys={list(evidence.keys())!r} "
                    f"contradicts law for pair ({patch_a!r}, {patch_b!r}): "
                    f"predicate='{law.predicate_description[:80]}'"
                )
        return None

    def compute_confidence(
        self, law: OverlapLaw, evidence: list[dict[str, Any]]
    ) -> float:
        """Compute the fraction of evidence items that satisfy the law's predicate.

        Parameters
        ----------
        law:
            The law to evaluate.
        evidence:
            List of evidence dicts.

        Returns
        -------
        float
            Fraction in [0.0, 1.0].  Returns 0.0 for empty evidence.
        """
        if not evidence:
            return 0.0

        probe = LawCandidate(
            predicate=law.predicate_description,
            patch_pair=law.patch_pair,
        )
        satisfied = sum(1 for item in evidence if probe.test_against(item))
        return satisfied / len(evidence)

    def recompute_law_confidence(
        self, law: OverlapLaw, evidence: list[dict[str, Any]]
    ) -> OverlapLaw:
        """Return a new OverlapLaw with confidence recomputed against *evidence*.

        Also updates support_count and violation_count based on the
        evidence pool test results.
        """
        if not evidence:
            return law

        probe = LawCandidate(
            predicate=law.predicate_description,
            patch_pair=law.patch_pair,
        )
        new_support = sum(1 for item in evidence if probe.test_against(item))
        new_violations = len(evidence) - new_support
        total = len(evidence)
        new_conf = new_support / total if total > 0 else 0.0

        from dataclasses import replace
        return replace(
            law,
            support_count=new_support,
            violation_count=new_violations,
            confidence=new_conf,
        )


# ---------------------------------------------------------------------------
# OverlapLawLibrary — curated collection
# ---------------------------------------------------------------------------


class OverlapLawLibrary:
    """Curated, indexed collection of verified overlap laws.

    The library is the authoritative store of overlap laws for the synthesis
    pipeline.  Unlike OverlapLawIndex (a working index within a single run),
    the library persists across runs and enforces a stability policy:
    only laws with stability ≥ STABLE are admitted as canonical entries.

    Laws with lower stability are stored in a *pending* tier and promoted
    when their stability is upgraded.

    The patch-pair index is bidirectional and order-independent: laws for
    pair (A, B) are also returned when querying (B, A).

    Attributes
    ----------
    _laws:
        Primary dict mapping law_id → OverlapLaw (canonical laws only).
    _pending:
        Dict of UNSTABLE or PROVISIONAL laws awaiting promotion.
    _patch_pair_index:
        Maps canonical pair (a ≤ b) → list of law_ids.
    _stability_counts:
        Tracks how many laws are at each stability level.
    """

    def __init__(self) -> None:
        self._laws: dict[str, OverlapLaw] = {}
        self._pending: dict[str, OverlapLaw] = {}
        self._patch_pair_index: dict[tuple[str, str], list[str]] = {}
        self._stability_counts: dict[LawStability, int] = {s: 0 for s in LawStability}

    def _canonical(self, a: str, b: str) -> tuple[str, str]:
        """Return (a, b) sorted lexicographically."""
        return (a, b) if a <= b else (b, a)

    def _update_stability_count(self, law: OverlapLaw, delta: int) -> None:
        """Increment (delta=+1) or decrement (delta=-1) stability counter."""
        self._stability_counts[law.stability] = max(
            0, self._stability_counts.get(law.stability, 0) + delta
        )

    @property
    def size(self) -> int:
        return self.law_count()

    def __len__(self) -> int:
        return self.law_count()

    def add_law(self, law: OverlapLaw | LawCandidate) -> None:
        """Add *law* to the library.

        If the law's stability is STABLE or PROVEN it goes into the canonical
        ``_laws`` dict.  Otherwise it goes into ``_pending``.

        Duplicate law_ids replace the existing entry.

        Parameters
        ----------
        law:
            The OverlapLaw to add.
        """
        if isinstance(law, LawCandidate):
            law = law.to_overlap_law()

        # Remove from wherever it currently lives
        self._remove_from_all(law.law_id)

        if law.is_stable():
            self._laws[law.law_id] = law
        else:
            self._pending[law.law_id] = law

        self._update_stability_count(law, +1)

        # Update the pair index
        pair_key = self._canonical(*law.patch_pair)
        if pair_key not in self._patch_pair_index:
            self._patch_pair_index[pair_key] = []
        if law.law_id not in self._patch_pair_index[pair_key]:
            self._patch_pair_index[pair_key].append(law.law_id)

        logger.debug(
            "OverlapLawLibrary.add_law: added %s (stability=%s)",
            law.law_id[:8],
            law.stability.value,
        )

    def _remove_from_all(self, law_id: str) -> OverlapLaw | None:
        """Remove law from _laws or _pending.  Returns the removed law or None."""
        law = self._laws.pop(law_id, None) or self._pending.pop(law_id, None)
        if law is not None:
            self._update_stability_count(law, -1)
            pair_key = self._canonical(*law.patch_pair)
            if pair_key in self._patch_pair_index:
                self._patch_pair_index[pair_key] = [
                    lid for lid in self._patch_pair_index[pair_key] if lid != law_id
                ]
                if not self._patch_pair_index[pair_key]:
                    del self._patch_pair_index[pair_key]
        return law

    def query(self, patch_a: str, patch_b: str | None = None) -> list[OverlapLaw] | OverlapLaw | None:
        """Return all laws (canonical + pending) for the given patch pair.

        The query is order-independent.

        Parameters
        ----------
        patch_a, patch_b:
            The two patch keys to look up.

        Returns
        -------
        list[OverlapLaw]
            All laws (stable and pending) for this pair, sorted by stability
            descending (PROVEN first).
        """
        if patch_b is None:
            return self._laws.get(patch_a) or self._pending.get(patch_a)

        pair_key = self._canonical(patch_a, patch_b)
        ids = self._patch_pair_index.get(pair_key, [])
        laws = []
        for lid in ids:
            law = self._laws.get(lid) or self._pending.get(lid)
            if law is not None:
                laws.append(law)
        # Sort: PROVEN > STABLE > PROVISIONAL > UNSTABLE
        stability_order = {
            LawStability.PROVEN: 3,
            LawStability.STABLE: 2,
            LawStability.PROVISIONAL: 1,
            LawStability.UNSTABLE: 0,
        }
        laws.sort(key=lambda l: stability_order.get(l.stability, 0), reverse=True)
        return laws

    def get_stable_laws(self) -> list[OverlapLaw]:
        """Return all canonical laws (stability ≥ STABLE).

        Returns
        -------
        list[OverlapLaw]
            All laws with STABLE or PROVEN stability, sorted by law_id.
        """
        return sorted(self._laws.values(), key=lambda l: l.law_id)

    def get_pending_laws(self) -> list[OverlapLaw]:
        """Return all pending laws (UNSTABLE or PROVISIONAL)."""
        return sorted(self._pending.values(), key=lambda l: l.law_id)

    def merge_library(self, other: "OverlapLawLibrary") -> None:
        """Merge all laws from *other* into this library.

        Self wins on law_id conflicts (same law_id but differing fields).
        New laws are added regardless of stability level.

        Parameters
        ----------
        other:
            Another OverlapLawLibrary whose laws will be incorporated.
        """
        for law in list(other._laws.values()) + list(other._pending.values()):
            if law.law_id not in self._laws and law.law_id not in self._pending:
                self.add_law(law)

    def merge(self, other: "OverlapLawLibrary") -> "OverlapLawLibrary":
        self.merge_library(other)
        return self

    def merge_index(self, index: OverlapLawIndex) -> None:
        """Merge all laws from an OverlapLawIndex into this library."""
        for law in index.all_laws():
            self.add_law(law)

    def remove_law(self, law_id: str) -> bool:
        """Remove the law with *law_id*.

        Returns True if the law existed and was removed.

        Parameters
        ----------
        law_id:
            The UUID of the law to remove.
        """
        law = self._remove_from_all(law_id)
        return law is not None

    def update_stability(self, law_id: str, new_stability: LawStability) -> None:
        """Update the stability of an existing law and reindex it.

        Parameters
        ----------
        law_id:
            The UUID of the law to update.
        new_stability:
            The new LawStability value.

        Raises
        ------
        KeyError
            If no law with *law_id* exists in either canonical or pending tier.
        """
        from dataclasses import replace as dc_replace

        law = self._laws.get(law_id) or self._pending.get(law_id)
        if law is None:
            raise KeyError(f"No law with id {law_id!r} in library.")
        updated = dc_replace(law, stability=new_stability)
        self._remove_from_all(law_id)
        self.add_law(updated)

    def promote_pending(self) -> int:
        """Promote all pending laws whose stability is now STABLE or PROVEN.

        Useful after calling update_stability() in a loop.

        Returns the number of laws promoted from pending to canonical.
        """
        promoted = 0
        for law_id in list(self._pending.keys()):
            law = self._pending[law_id]
            if law.is_stable():
                self._remove_from_all(law_id)
                self.add_law(law)
                promoted += 1
        return promoted

    def demote_by_violations(self, max_violation_rate: float = 0.15) -> int:
        """Demote canonical laws whose violation rate exceeds *max_violation_rate*.

        Demoted laws move from _laws to _pending.

        Returns
        -------
        int
            Number of laws demoted.
        """
        demoted = 0
        for law_id, law in list(self._laws.items()):
            if (
                law.observation_count() >= 3
                and law.violation_rate() > max_violation_rate
            ):
                demoted_law = law.demote_stability()
                self._remove_from_all(law_id)
                self.add_law(demoted_law)
                demoted += 1
        return demoted

    def law_count(self) -> int:
        """Return the total number of laws (canonical + pending)."""
        return len(self._laws) + len(self._pending)

    def canonical_law_count(self) -> int:
        """Return the number of canonical (stable) laws."""
        return len(self._laws)

    def pending_law_count(self) -> int:
        """Return the number of pending (sub-stable) laws."""
        return len(self._pending)

    def patch_pair_count(self) -> int:
        """Return the number of distinct patch pairs with at least one law."""
        return len(self._patch_pair_index)

    def stats(self) -> dict[str, Any]:
        """Return a summary dictionary with counts by stability level.

        Returns
        -------
        dict with keys:
            total_laws       — int, total law count
            canonical_laws   — int, stable + proven laws
            pending_laws     — int, unstable + provisional laws
            patch_pairs      — int, distinct patch pairs covered
            by_stability     — dict mapping stability name to count
        """
        by_stability = {s.value: self._stability_counts.get(s, 0) for s in LawStability}
        return {
            "total_laws": self.law_count(),
            "canonical_laws": self.canonical_law_count(),
            "pending_laws": self.pending_law_count(),
            "patch_pairs": self.patch_pair_count(),
            "by_stability": by_stability,
        }

    def export_to_dict(self) -> dict[str, Any]:
        """Export the entire library to a serialisable dict.

        Returns
        -------
        dict with keys:
            canonical — list of dicts for canonical laws
            pending   — list of dicts for pending laws
        """
        def law_to_dict(law: OverlapLaw) -> dict[str, Any]:
            return {
                "law_id": law.law_id,
                "patch_pair": list(law.patch_pair),
                "predicate_description": law.predicate_description,
                "stability": law.stability.value,
                "support_count": law.support_count,
                "violation_count": law.violation_count,
                "confidence": law.confidence,
                "discovered_in_record_id": law.discovered_in_record_id,
                "provenance": list(law.provenance),
            }

        return {
            "canonical": [law_to_dict(law) for law in self.get_stable_laws()],
            "pending": [law_to_dict(law) for law in self.get_pending_laws()],
        }

    def covers_pair(self, patch_a: str, patch_b: str) -> bool:
        """Return True iff this library has at least one law for the given pair."""
        return bool(self.query(patch_a, patch_b))

    def all_patch_keys(self) -> frozenset[str]:
        """Return the frozenset of all patch keys appearing in any law."""
        keys: set[str] = set()
        for pair_key in self._patch_pair_index:
            keys.update(pair_key)
        return frozenset(keys)

    def __repr__(self) -> str:
        return (
            f"OverlapLawLibrary("
            f"canonical={self.canonical_law_count()}, "
            f"pending={self.pending_law_count()}, "
            f"pairs={self.patch_pair_count()})"
        )


# ---------------------------------------------------------------------------
# OverlapLawDiscovery — orchestrates the mine → test → generalize → promote pipeline
# ---------------------------------------------------------------------------


class OverlapLawDiscovery:
    """Discovers overlap laws from synthesis records.

    Implements Algorithm 41.1 of theory2.tex §41.5.  The pipeline is:

    1. mine_patterns()     — scan one or more synthesis records for recurring
                             (patch_pair, predicate) patterns.
    2. _generalize_pattern() — make each pattern more abstract until confidence
                             drops below min_law_confidence.
    3. _filter_candidates() — discard candidates below confidence threshold.
    4. verify (via LawVerifier) — check candidates against treaties.
    5. Promote to OverlapLaw and add to the library.

    Attributes
    ----------
    _candidates:
        Working list of LawCandidate objects under evaluation.
    _verifier:
        LawVerifier used during the verify step.
    _library:
        The OverlapLawLibrary that receives promoted laws.
    config:
        SynthesisConfig controlling thresholds and limits.
    """

    def __init__(
        self,
        library: OverlapLawLibrary | None = None,
        config: SynthesisConfig | None = None,
    ) -> None:
        self._candidates: list[LawCandidate] = []
        self._verifier: LawVerifier = LawVerifier()
        self._library: OverlapLawLibrary = library or OverlapLawLibrary()
        self.config: SynthesisConfig = config or DEFAULT_CONFIG

    def discover(
        self,
        synthesis_record: HypercoverSynthesisRecord,
        evidence_pool: list[dict[str, Any]] | None = None,
        treaties: list[Any] | None = None,
    ) -> list[OverlapLaw]:
        """Run the full discovery pipeline on a single synthesis record.

        Parameters
        ----------
        synthesis_record:
            A completed HypercoverSynthesisRecord with cover_patch_keys and
            overlap_pairs populated.
        evidence_pool:
            Optional list of evidence dicts for counterexample search.
        treaties:
            Optional list of OverlapTreaty objects for compatibility checking.

        Returns
        -------
        list[OverlapLaw]
            Newly promoted stable overlap laws added to the library during
            this discovery run.
        """
        evidence_pool = evidence_pool or []
        treaties = treaties or []

        # Step 1: Mine candidates from the single record
        candidates = self.mine_patterns([synthesis_record])

        # Step 2: Test against evidence pool
        for candidate in candidates:
            for ev in evidence_pool:
                if candidate.test_against(ev):
                    candidate.add_support(str(id(ev)))
                else:
                    candidate.add_counterexample(str(id(ev)))

        # Step 3: Generalise each candidate
        generalised: list[LawCandidate] = []
        for candidate in candidates:
            gen = self._generalize_pattern(candidate)
            generalised.append(gen)

        # Step 4: Filter by confidence
        filtered = self._filter_candidates(generalised)

        # Step 5: Verify against treaties and promote
        promoted_laws: list[OverlapLaw] = []
        for candidate in filtered:
            law = candidate.to_overlap_law()
            verified, cex = self._verifier.verify(law, treaties)
            if not verified:
                logger.debug(
                    "Candidate %s rejected by verifier: %d contradictions",
                    candidate.candidate_id[:8],
                    len(cex),
                )
                continue
            # Optionally check evidence pool for counterexamples
            if evidence_pool:
                cex_str = self._verifier.find_counterexample(law, evidence_pool)
                if cex_str:
                    logger.debug("Candidate %s has counterexample: %s", candidate.candidate_id[:8], cex_str)
                    continue
                law = self._verifier.recompute_law_confidence(law, evidence_pool)

            stability = self._compute_stability(law)
            from dataclasses import replace as dc_replace
            law = dc_replace(
                law,
                stability=stability,
                discovered_in_record_id=synthesis_record.record_id,
                provenance=synthesis_record.provenance,
            )
            self._library.add_law(law)
            promoted_laws.append(law)

        logger.info(
            "OverlapLawDiscovery.discover: %d candidates → %d promoted laws",
            len(candidates),
            len(promoted_laws),
        )
        self._candidates.extend(generalised)
        return promoted_laws

    def mine_patterns(
        self, history: list[HypercoverSynthesisRecord]
    ) -> list[LawCandidate]:
        """Mine LawCandidate objects from a list of synthesis records.

        For each overlap pair that appears in at least one record, creates
        a LawCandidate with a template predicate.  Pairs that appear in
        multiple records receive a confidence bonus proportional to their
        frequency.

        Parameters
        ----------
        history:
            List of completed (or in-progress) synthesis records.

        Returns
        -------
        list[LawCandidate]
            One LawCandidate per unique patch pair, sorted by confidence desc.
        """
        # Count how many records each pair appears in
        pair_counts: dict[tuple[str, str], int] = {}
        pair_record_ids: dict[tuple[str, str], list[str]] = {}

        for record in history:
            for pair in record.overlap_pairs:
                canonical = tuple(sorted(pair))
                key = (canonical[0], canonical[1])
                pair_counts[key] = pair_counts.get(key, 0) + 1
                if key not in pair_record_ids:
                    pair_record_ids[key] = []
                if record.record_id not in pair_record_ids[key]:
                    pair_record_ids[key].append(record.record_id)

        total_records = max(len(history), 1)
        candidates: list[LawCandidate] = []

        for (patch_a, patch_b), count in pair_counts.items():
            # Confidence scales with how frequently this pair appeared
            frequency = count / total_records
            base_confidence = min(0.95, 0.4 + 0.5 * frequency)

            predicate = (
                f"For any local section on patch '{patch_a}' and any local section "
                f"on patch '{patch_b}', their restrictions to the intersection "
                f"'{patch_a} ∩ {patch_b}' are compatible."
            )

            candidate = LawCandidate(
                predicate=predicate,
                confidence=base_confidence,
                patch_pair=(patch_a, patch_b),
            )
            # Add record IDs as supporting evidence
            for rec_id in pair_record_ids[(patch_a, patch_b)]:
                candidate.add_support(rec_id)

            candidates.append(candidate)

        # Sort by confidence descending
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        logger.debug("mine_patterns: produced %d candidates from %d records", len(candidates), len(history))
        return candidates

    def _generalize_pattern(self, pattern: LawCandidate) -> LawCandidate:
        """Apply generalisation until confidence drops below threshold or max depth.

        Applies generalize_predicate() iteratively.  At each step, confidence
        decreases by 5% per level (due to the decay in LawCandidate.generalize()).
        Stops when:
        - confidence drops below config.min_law_confidence, OR
        - generalization_level reaches config.overlap_generalization_depth

        Parameters
        ----------
        pattern:
            A mutable LawCandidate.

        Returns
        -------
        LawCandidate
            The most abstract version that still meets the confidence threshold,
            or the original if generalisation immediately drops below threshold.
        """
        current = pattern
        max_depth = self.config.overlap_generalization_depth
        threshold = self.config.min_law_confidence

        best = current
        for _ in range(max_depth):
            generalised = current.generalize()
            if generalised.confidence < threshold:
                # Keep best (the version just before this drop)
                break
            best = generalised
            current = generalised

        return best

    def _compute_stability(self, law: OverlapLaw) -> LawStability:
        """Compute the stability level for a newly promoted law.

        Logic (theory2.tex §41.5.3):
        - confidence < 0.4: UNSTABLE
        - 0.4 ≤ confidence < 0.7: PROVISIONAL
        - 0.7 ≤ confidence < 0.9 AND support_count ≥ min_support_count: STABLE
        - confidence ≥ 0.9 AND support_count ≥ min_support_count: PROVEN
        - Otherwise: PROVISIONAL (conservative fallback)

        Parameters
        ----------
        law:
            An OverlapLaw awaiting stability classification.

        Returns
        -------
        LawStability
        """
        min_support = self.config.min_support_count
        if law.confidence < 0.4:
            return LawStability.UNSTABLE
        if law.confidence < 0.7:
            return LawStability.PROVISIONAL
        if law.support_count < min_support:
            return LawStability.PROVISIONAL
        if law.confidence >= 0.9 and law.support_count >= min_support:
            return LawStability.PROVEN
        return LawStability.STABLE

    def _score_candidate(self, candidate: LawCandidate) -> float:
        """Compute a composite score for candidate ranking.

        The score combines:
        - confidence       (primary signal)
        - log-support bonus (breadth of evidence)
        - generalization bonus (more abstract = more useful)
        - counterexample penalty (correctness)

        Parameters
        ----------
        candidate:
            A LawCandidate.

        Returns
        -------
        float
            Composite score in approximately [0, 1.5].
        """
        import math
        support_bonus = 0.1 * math.log1p(candidate.support_count())
        gen_bonus = 0.05 * candidate.generalization_level
        cex_penalty = 0.15 * candidate.counterexample_count()
        return candidate.confidence + support_bonus + gen_bonus - cex_penalty

    def _filter_candidates(
        self, candidates: list[LawCandidate]
    ) -> list[LawCandidate]:
        """Keep only candidates above the confidence threshold.

        Also rejects candidates that have more counterexamples than the
        configured maximum.

        Parameters
        ----------
        candidates:
            Input list of LawCandidates.

        Returns
        -------
        list[LawCandidate]
            Filtered and re-sorted list (by composite score descending).
        """
        threshold = self.config.min_law_confidence
        max_cex = self.config.max_counterexamples_before_reject

        filtered = [
            c
            for c in candidates
            if c.confidence >= threshold and c.counterexample_count() <= max_cex
        ]
        # Re-sort by composite score
        filtered.sort(key=self._score_candidate, reverse=True)

        logger.debug(
            "_filter_candidates: %d → %d (threshold=%.2f, max_cex=%d)",
            len(candidates),
            len(filtered),
            threshold,
            max_cex,
        )
        return filtered

    def get_library(self) -> OverlapLawLibrary:
        """Return the OverlapLawLibrary that accumulates promoted laws."""
        return self._library

    def pending_candidates(self) -> list[LawCandidate]:
        """Return the current working list of all candidates seen so far."""
        return list(self._candidates)

    def clear_candidates(self) -> None:
        """Discard all accumulated working candidates."""
        self._candidates.clear()

    def library_stats(self) -> dict[str, Any]:
        """Return statistics about the current library state."""
        return self._library.stats()
